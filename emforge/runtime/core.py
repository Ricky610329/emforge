"""emforge/runtime/core.py — `Runtime`：單例鎖、設定重載、state／status、tick 骨架、run。

state.json（runtime 自己的持久狀態，重啟重讀）與 status.json（每 tick 導出、人讀）分開：人改 status 不會汙染狀態。
兩個根：`root`＝本機程式碼／設定根（registry.py、strategies/、策略 workdir）；`depot`＝共享協調狀態（預設 `FileDepot(root)`）。
鎖＝`Depot` 租約（claim／touch／break_if_stale／release(owner=)）；strategies.yaml 重載比**內容 sha1**，不靠 mtime。
"""
import os
import threading
import time
from pathlib import Path

from .. import _version, events, netid, paths, profiles, strategy
from ..batches import Batch
from ..db import Database
from ..depot import FileDepot, open_depot
from ..model import now_iso, sha1_hex
from ..queue import Queue
from . import collect as _collect
from . import notarize as _notarize
from . import reconcile as _reconcile
from . import schedule as _schedule


class RuntimeLocked(Exception):
    """同一 profile 已有 runtime 在跑（D2：一實例一 profile）。"""


def default_state() -> dict:
    return {"tick": 0, "strategies": {}, "paused_profile": None, "notarize": {}, "seed_base": 0}


def default_strategy_state() -> dict:
    return {"errors_consecutive": 0, "paused": False, "n_dispatched": 0, "last_dispatch_tick": None}


class _Heartbeat(threading.Thread):
    """背景每 interval_s touch 鎖——一個 tick 可能超過 stale 門檻（策略子行程逐個逾時），不能只靠 tick 開頭心跳（review-7）。"""

    def __init__(self, fn, interval_s: float):
        super().__init__(daemon=True, name="emforge-heartbeat")
        self._fn, self._interval, self._halt = fn, interval_s, threading.Event()   # 不叫 _stop：Thread 內部有同名方法

    def run(self) -> None:
        while not self._halt.wait(self._interval):
            try:
                self._fn()
            except Exception:  # noqa: BLE001 — 心跳失敗不能炸執行緒；鎖變 stale 會被下一個實例看到
                pass

    def stop(self) -> None:
        self._halt.set()
        self.join(timeout=5)


class Runtime:
    def __init__(self, root, profile_name: str, *, depot=None, sleep=time.sleep, propose_fn=None,
                 machine_tag: str | None = None, readonly: bool = False, heartbeat_s: float = 30.0):
        """`readonly=True`：CLI（smoke／abandon）用——不拿鎖、不寫 state.json（review-4：別用舊快照覆寫跑著的 runtime）。"""
        self.root, self.profile_name = Path(root), profile_name
        self.depot = open_depot(depot) if depot is not None else FileDepot(self.root)
        self.readonly, self.heartbeat_s, self._hb = readonly, heartbeat_s, None
        profiles.load_user_registry(self.root)
        self.profile = profiles.get_profile(profile_name)
        self.db = Database(self.depot, write_profile=profile_name)
        self.queue = Queue(self.depot)
        self._sleep = sleep
        self._propose = propose_fn or strategy.propose_in_subprocess
        self.machine_tag = machine_tag or netid.local_tag()
        self._lock_owner = f"{self.machine_tag}:{os.getpid()}:{os.urandom(4).hex()}"   # 每個實例獨一：release 只刪自己的
        self.events_key = paths.events_jsonl(profile_name)
        self.yaml_key = paths.strategies_yaml(profile_name)
        text = self.depot.get_bytes(self.yaml_key)
        if text is None:
            raise strategy.ConfigError(f"缺 {self.depot.spec}/{self.yaml_key}——runtime 沒有策略清單不能起（`emforge init` 會給範本）")
        self.config = strategy.parse_strategies_yaml(text.decode("utf-8"), profile=profile_name)
        self._config_sha = sha1_hex(text)
        self.state = self.depot.get_json(paths.state_json(profile_name)) or default_state()
        self._locked = False

    # ── 鎖（租約；心跳＝touch） ───────────────────────────────────────────
    def acquire_lock(self) -> None:
        if self.readonly:
            raise RuntimeError("readonly runtime 不拿鎖")
        key = paths.runtime_lock(self.profile_name)
        stale_s = max(600.0, 5.0 * self.config.runtime.tick_s)
        payload = {"owner": self._lock_owner, "pid": os.getpid(), "at": now_iso(), "machine": self.machine_tag}
        for _ in range(3):
            if self.depot.claim(key, payload):
                self._locked = True
                return
            if self.depot.is_stale(key, stale_s):
                self.depot.break_if_stale(key, stale_s)      # 沒破到＝別人先破了，下一圈 claim 決勝負
                continue
            raise RuntimeLocked(f"profile {self.profile_name} 已有 runtime 在跑（{self.depot.owner(key)}）——一實例一 profile")
        raise RuntimeLocked(f"profile {self.profile_name} 的鎖搶不到：{self.depot.spec}/{key}")

    def heartbeat(self) -> None:
        """`touch` 缺即 no-op：鎖被破後心跳不會重建一個空鎖擋人。"""
        self.depot.touch(paths.runtime_lock(self.profile_name))

    def start_heartbeat(self) -> None:
        if not self._locked:
            raise RuntimeError("沒拿鎖不心跳")
        if self._hb is None:
            self._hb = _Heartbeat(self.heartbeat, self.heartbeat_s)
            self._hb.start()

    def stop_heartbeat(self) -> None:
        if self._hb is not None:
            self._hb.stop()
            self._hb = None

    def release_lock(self) -> None:
        self.stop_heartbeat()
        if self._locked:
            self.depot.release(paths.runtime_lock(self.profile_name), owner=self._lock_owner)   # 只刪自己的
            self._locked = False

    # ── 事件／設定／狀態 ────────────────────────────────────────────────
    def event(self, event: str, /, **fields) -> None:
        events.emit(self.depot, self.events_key, event, **fields)

    def reload_config(self) -> None:
        """yaml **內容** 變了才重讀（sha1，不靠 mtime）；無效 → config_invalid 事件、沿用上次有效（不停 runtime）。"""
        text = self.depot.get_bytes(self.yaml_key)
        if text is None:
            if self._config_sha is not None:
                self._config_sha = None
                self.event("config_invalid", error="strategies.yaml 不見了；沿用上次有效")
            return
        sha = sha1_hex(text)
        if sha == self._config_sha:
            return
        self._config_sha = sha
        try:
            cfg = strategy.parse_strategies_yaml(text.decode("utf-8"), profile=self.profile_name)
        except Exception as e:  # noqa: BLE001 — 人手改壞 yaml 不能讓整晚停
            self.event("config_invalid", error=f"{type(e).__name__}: {e}")
            return
        self.config = cfg
        self.event("config_reloaded", n_strategies=len(cfg.strategies))

    def strategy_state(self, name: str) -> dict:
        return self.state["strategies"].setdefault(name, default_strategy_state())

    def save_state(self) -> None:
        if self.readonly:
            raise RuntimeError("readonly runtime 不寫 state.json")
        self.depot.put_json(paths.state_json(self.profile_name), self.state)

    def inflight(self) -> list:
        """列舉可最終一致：列到了但已被 collect／abandon 拿掉的（get 回 None）直接跳過。"""
        out = []
        for key in self.depot.list(paths.inflight_dir(self.profile_name)):
            if key.endswith("/") or not key.endswith(".json"):
                continue
            doc = self.depot.get_json(key)
            if doc is not None:
                out.append(doc)
        return out

    def fleet_quiet(self) -> bool:
        """有派出去的批、但 quiet_s 內整個機隊沒有任何產出 → 只等，不排程（排隊 ≠ 停滯）。"""
        infl = self.inflight()
        if not infl:
            return False
        newest = 0.0
        for inf in infl:
            m = self.depot.modified_at(paths.inflight_file(self.profile_name, inf["store"])) or 0.0
            r = Batch(self.depot, inf["store"]).newest_result_at() or 0.0
            newest = max(newest, m, r)
        return (self.depot.now() - newest) > self.config.runtime.quiet_s

    def apply_control(self) -> None:
        """消費 CLI 寫的 control.json（resume 策略／profile）並刪除；runtime 每 tick 開頭呼叫。"""
        key = paths.control_json(self.profile_name)
        ctl = self.depot.get_json(key)
        if not ctl:
            return
        self.depot.delete(key)
        by = ctl.get("by", "cli")
        for name in ctl.get("resume_strategies", []):
            st = self.strategy_state(name)
            st["paused"], st["errors_consecutive"] = False, 0
            self.event("strategy_resumed", name=name, by=by)
        if ctl.get("resume_profile"):
            self.state["paused_profile"] = None
            self.event("profile_resumed", by=by)

    # ── tick ────────────────────────────────────────────────────────────
    def tick(self) -> None:
        self.state["tick"] += 1
        self.save_state()                    #! tick 號一加就落地：中途死掉重啟不重播同一號（review-3，store 名撞批）
        self.heartbeat()
        self.apply_control()
        self.reload_config()
        new = _collect.collect(self)
        _notarize.notarize_step(self, new)
        self.save_state()                    # 公證登記不能只在記憶體（死在 schedule 裡會丟）
        if self.fleet_quiet():
            self.event("fleet_quiet", quiet_s=self.config.runtime.quiet_s)
        else:
            _schedule.schedule(self)
        self.save_state()
        self.write_status()

    def write_status(self) -> None:
        now = self.depot.now()
        infl = self.inflight()
        strategies = {}
        for sc in self.config.strategies:
            st = self.strategy_state(sc.name)
            strategies[sc.name] = {"enabled": sc.enabled, "paused": st["paused"], "prio": sc.prio, "batch": sc.batch,
                                   "inflight": [i["store"] for i in infl if i["strategy"] == sc.name],
                                   "errors_consecutive": st["errors_consecutive"], "n_dispatched": st["n_dispatched"],
                                   "last_dispatch_tick": st["last_dispatch_tick"]}
        metas = self.db.metas(self.profile_name)
        status = {
            "profile": self.profile_name, "profile_hash": self.profile.profile_hash, "runtime_ver": _version.describe(),
            "pid": os.getpid(), "machine": self.machine_tag, "tick": self.state["tick"], "last_tick_at": now_iso(),
            "paused_profile": self.state.get("paused_profile"), "strategies": strategies,
            "inflight": [{"store": i["store"], "strategy": i["strategy"], "kind": i["kind"], "n": len(i["ids"]),
                          "n_collected": len(i["collected"]), "queue_state": self.queue.state(i["store"]),
                          "age_s": round(now - (self.depot.modified_at(paths.inflight_file(self.profile_name, i["store"])) or now))}
                         for i in infl],
            "notarize_in_progress": sorted(self.state.get("notarize", {})),
            "pending_count": len(self.depot.read_log(paths.pending_jsonl(self.profile_name))),
            "db": {"n_records": len(metas), "n_done": sum(1 for m in metas if m["status"] == "done")},
        }
        self.depot.put_json(paths.status_json(self.profile_name), status)

    # ── run ─────────────────────────────────────────────────────────────
    def run(self, once: bool = False) -> int:
        """0＝正常停；2＝對帳不一致拒起（I-14）。鎖被占直接拋 RuntimeLocked（CLI 轉 3）。"""
        self.acquire_lock()
        try:
            self.start_heartbeat()
            self.event("runtime_start", runtime_ver=_version.describe(), profile_hash=self.profile.profile_hash,
                       pid=os.getpid(), machine=self.machine_tag)
            repaired = self.db.refresh(self.profile_name)     # 索引 append 前死掉的檔補回去（review-8）
            if repaired:
                self.event("index_repaired", n=repaired)
            problems = _reconcile.reconcile(self)
            if problems:
                self.event("reconcile_mismatch", detail="; ".join(problems))
                self.event("runtime_stop", reason="reconcile_mismatch")
                return 2
            while True:
                if self.depot.exists(paths.runtime_stop(self.profile_name)):
                    self.event("runtime_stop", reason="stop_file")
                    return 0
                self.tick()
                if once:
                    self.event("runtime_stop", reason="once")
                    return 0
                self._sleep(self.config.runtime.tick_s)
        finally:
            self.release_lock()
