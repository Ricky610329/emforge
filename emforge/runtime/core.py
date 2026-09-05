"""emforge/runtime/core.py — `Runtime`：單例鎖、設定重載、state／status、tick 骨架、run。

state.json（runtime 自己的持久狀態，重啟重讀）與 status.json（每 tick 導出、人讀）分開：人改 status 不會汙染狀態。
"""
import os
import time
from pathlib import Path

from .. import _version, events, fs, netid, paths, profiles, strategy
from ..batches import Batch
from ..db import Database
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


class Runtime:
    def __init__(self, root, profile_name: str, *, sleep=time.sleep, propose_fn=None, machine_tag: str | None = None):
        self.root, self.profile_name = Path(root), profile_name
        profiles.load_user_registry(self.root)
        self.profile = profiles.get_profile(profile_name)
        self.db = Database(self.root, write_profile=profile_name)
        self.queue = Queue(self.root)
        self._sleep = sleep
        self._propose = propose_fn or strategy.propose_in_subprocess
        self.machine_tag = machine_tag or netid.local_tag()
        self.events_path = paths.events_jsonl(self.root, profile_name)
        yaml_path = paths.strategies_yaml(self.root, profile_name)
        if not yaml_path.exists():
            raise strategy.ConfigError(f"缺 {yaml_path}——runtime 沒有策略清單不能起（`emforge init` 會給範本）")
        self.config = strategy.load_strategies_yaml(yaml_path, profile=profile_name)
        self._config_mtime = fs.mtime(yaml_path)
        self.state = fs.read_json(paths.state_json(self.root, profile_name), default=None) or default_state()
        self._locked = False

    # ── 鎖（心跳＝tick 時 touch） ────────────────────────────────────────
    def acquire_lock(self) -> None:
        lk = paths.runtime_lock(self.root, self.profile_name)
        stale_s = max(600.0, 5.0 * self.config.runtime.tick_s)
        payload = {"pid": os.getpid(), "at": fs.now_iso(), "machine": self.machine_tag}
        for _ in range(3):
            if fs.try_claim(lk, payload):
                self._locked = True
                return
            if fs.is_stale(lk, stale_s):
                try:
                    os.replace(lk, lk.with_name(f"lock.broken.{os.getpid()}"))
                except (FileNotFoundError, PermissionError):
                    pass
                continue
            raise RuntimeLocked(f"profile {self.profile_name} 已有 runtime 在跑（{fs.read_claim(lk)}）——一實例一 profile")
        raise RuntimeLocked(f"profile {self.profile_name} 的鎖搶不到：{lk}")

    def heartbeat(self) -> None:
        fs.touch(paths.runtime_lock(self.root, self.profile_name))

    def release_lock(self) -> None:
        if self._locked:
            fs.release(paths.runtime_lock(self.root, self.profile_name))
            self._locked = False

    # ── 事件／設定／狀態 ────────────────────────────────────────────────
    def event(self, event: str, /, **fields) -> None:
        events.emit(self.events_path, event, **fields)

    def reload_config(self) -> None:
        """yaml mtime 變了才重讀；無效 → config_invalid 事件、沿用上次有效（不停 runtime）。"""
        y = paths.strategies_yaml(self.root, self.profile_name)
        m = fs.mtime(y)
        if m == self._config_mtime:
            return
        self._config_mtime = m
        try:
            cfg = strategy.load_strategies_yaml(y, profile=self.profile_name)
        except Exception as e:  # noqa: BLE001 — 人手改壞 yaml 不能讓整晚停
            self.event("config_invalid", error=f"{type(e).__name__}: {e}")
            return
        self.config = cfg
        self.event("config_reloaded", n_strategies=len(cfg.strategies))

    def strategy_state(self, name: str) -> dict:
        return self.state["strategies"].setdefault(name, default_strategy_state())

    def save_state(self) -> None:
        fs.atomic_write_json(paths.state_json(self.root, self.profile_name), self.state)

    def inflight(self) -> list:
        d = paths.inflight_dir(self.root, self.profile_name)
        return [fs.read_json(p) for p in sorted(d.glob("*.json"))] if d.is_dir() else []

    def fleet_quiet(self) -> bool:
        """有派出去的批、但 quiet_s 內整個機隊沒有任何產出 → 只等，不排程（排隊 ≠ 停滯）。"""
        infl = self.inflight()
        if not infl:
            return False
        newest = 0.0
        for inf in infl:
            m = fs.mtime(paths.inflight_file(self.root, self.profile_name, inf["store"])) or 0.0
            r = Batch(self.root, inf["store"]).newest_result_mtime() or 0.0
            newest = max(newest, m, r)
        return (time.time() - newest) > self.config.runtime.quiet_s

    def apply_control(self) -> None:
        """消費 CLI 寫的 control.json（resume 策略／profile）並刪除；runtime 每 tick 開頭呼叫。"""
        p = paths.control_json(self.root, self.profile_name)
        ctl = fs.read_json(p, default=None)
        if not ctl:
            return
        fs.release(p)
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
        self.heartbeat()
        self.apply_control()
        self.reload_config()
        new = _collect.collect(self)
        _notarize.notarize_step(self, new)
        if self.fleet_quiet():
            self.event("fleet_quiet", quiet_s=self.config.runtime.quiet_s)
        else:
            _schedule.schedule(self)
        self.save_state()
        self.write_status()

    def write_status(self) -> None:
        now = time.time()
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
            "pid": os.getpid(), "machine": self.machine_tag, "tick": self.state["tick"], "last_tick_at": fs.now_iso(),
            "paused_profile": self.state.get("paused_profile"), "strategies": strategies,
            "inflight": [{"store": i["store"], "strategy": i["strategy"], "kind": i["kind"], "n": len(i["ids"]),
                          "n_collected": len(i["collected"]), "queue_state": self.queue.state(i["store"]),
                          "age_s": round(now - (fs.mtime(paths.inflight_file(self.root, self.profile_name, i["store"])) or now))}
                         for i in infl],
            "notarize_in_progress": sorted(self.state.get("notarize", {})),
            "pending_count": len(fs.read_jsonl(paths.pending_jsonl(self.root, self.profile_name))),
            "db": {"n_records": len(metas), "n_done": sum(1 for m in metas if m["status"] == "done")},
        }
        fs.atomic_write_json(paths.status_json(self.root, self.profile_name), status)

    # ── run ─────────────────────────────────────────────────────────────
    def run(self, once: bool = False) -> int:
        """0＝正常停；2＝對帳不一致拒起（I-14）。鎖被占直接拋 RuntimeLocked（CLI 轉 3）。"""
        self.acquire_lock()
        try:
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
                if paths.runtime_stop(self.root, self.profile_name).exists():
                    self.event("runtime_stop", reason="stop_file")
                    return 0
                self.tick()
                if once:
                    self.event("runtime_stop", reason="once")
                    return 0
                self._sleep(self.config.runtime.tick_s)
        finally:
            self.release_lock()
