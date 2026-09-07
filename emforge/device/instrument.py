"""emforge/device/instrument.py — `Instrument`：一台正式機上的模擬器，MHS 式的「裝置」。

  states      idle ─open→ opening ─ok→ ready ─simulate→ busy ─→ ready ─close→ idle；open 失敗 → fault（close → idle）；
              任一層急停 → estop（open／simulate 拋 EstopEngaged；解除後 → idle）
  procedures  bind／open／simulate／kill／close（Simulator 協定直傳：run_batch 把 Instrument 當 sim 用）、abort、selfcheck、simulate_once
  lease       acquire(owner)／release(owner)：非阻塞、一次一件；worker 用 `queue:<store>`（同一批續跑可重入）、
              MCP 用一次性 `mcp:<by>:<rand>`（**不重入**）；撞到＝拒＋lease_refused
  safety      Limits／check_preconditions（open 前）／e-stop 硬檢查／兩段式 confirm（token 每次不同、綁 op/key、10 分鐘到期、單次）

session 全程同一執行緒（COM apartment）；`kill()` 不經鎖、可跨執行緒。狀態字典只有這裡寫（轉換即寫＋心跳）；
裝置日誌單寫者（`devices/<tag>/log.jsonl`）。`simulate_once` 結果與批結果同格式、**不入 db**（要 provenance 用 `smoke`）。
"""
import hashlib
import os
import secrets
import statistics
import threading
import time
from pathlib import Path

import numpy as np

from .. import doctor, events, paths, profiles
from ..batches import error_result, make_result, result_base
from ..depot import FileDepot, open_depot
from ..heartbeat import Heartbeat
from ..model import now_iso, record_id
from ..worker.guard import guarded_call, open_with_retries
from ..worker.workdir import WorkDir, default_work_root
from . import estop, reference
from .limits import Limits, check_preconditions, load_limits
from .states import DeviceState, write_state

HISTORY_MAX = 50
RECENT_MAX = 50
REFERENCE_REFRESH_S = 3600.0


class DeviceBusy(Exception):
    """租約被別人持有。"""


class PreconditionFailed(Exception):
    """前置檢查不過（清單在 `problems`）。"""

    def __init__(self, problems: list):
        self.problems = list(problems)
        super().__init__("；".join(self.problems))


class ConfirmRejected(Exception):
    """兩段式確認的 token 錯、過期或用過。"""


class Instrument:
    def __init__(self, root, tag: str, *, depot=None, sim_factory=None, limits: Limits | None = None,
                 heartbeat_s: float = 30.0, worker_ver: str = "", work_root=None, secret: str | None = None,
                 sleep=time.sleep, clock=time.time):
        self.root, self.tag = Path(root), tag
        self.depot = open_depot(depot) if depot is not None else FileDepot(self.root)
        self._factory = sim_factory or (lambda workdir, profile: profiles.make_simulator(profile, workdir))
        #? limits 沒給就讀 <root>/limits.json（部署設定，M16）；給了＝呼叫端政策（測試／嵌入）。來源進說明檔。
        self.limits, self.limits_source = (limits, "explicit") if limits is not None else load_limits(self.root)
        self.work = WorkDir(work_root or default_work_root())
        self.heartbeat_s, self._sleep, self._clock = float(heartbeat_s), sleep, clock
        self._secret = secret or os.environ.get("EMFORGE_DEVICE_TOKEN") or os.urandom(16).hex()
        self.state = DeviceState(tag=tag, worker_ver=worker_ver, pid=os.getpid(), started_at=now_iso(),
                                 limits=self.limits.to_dict())
        self.history: list = []                  # [(state, at)]，最近 HISTORY_MAX 筆
        self.recent_time_s: list = []
        self._lock = threading.RLock()           # 狀態字典與租約
        self._owner: str | None = None
        self.sim = None
        self._bound: tuple | None = None         # (profile, workdir, store)
        self._issued: dict = {}                  # confirm token → (op, key, expires_at)；消費即移除
        self._hb: Heartbeat | None = None
        self._opened_once = False
        self._reference_at: float | None = None

    # ── 日誌／狀態 ──────────────────────────────────────────────────────────
    def log(self, event: str, /, **fields) -> None:
        try:
            events.emit(self.depot, paths.device_log(self.tag), event, tag=self.tag, **fields)
        except Exception:  # noqa: BLE001 — 日誌寫不進去不能殺儀器
            pass

    def _set(self, state: str | None = None, **changes) -> None:
        with self._lock:
            if state is not None and state != self.state.state:
                self.state.state = state
                self.history.append((state, now_iso()))
                del self.history[:-HISTORY_MAX]
            for k, v in changes.items():
                setattr(self.state, k, v)
            write_state(self.depot, self.state, log=self.log)

    def _beat(self) -> bool:
        self.estop_engaged()
        self._set()
        if self._reference_at is not None and self._clock() - self._reference_at >= REFERENCE_REFRESH_S:
            self.refresh_reference()
        return True

    def refresh_reference(self) -> bool:
        """刷 `devices/<tag>/reference.{md,json}`（open 成功後與每小時）。"""
        self._reference_at = self._clock()
        return reference.write_reference(self)

    # ── 生命週期 ────────────────────────────────────────────────────────────
    def start(self) -> None:
        self.history.append((self.state.state, now_iso()))
        self._set(free_gb=None)
        self.log("device_start", worker_ver=self.state.worker_ver, pid=os.getpid())
        if self._hb is None:
            self._hb = Heartbeat(self._beat, self.heartbeat_s, name=f"emforge-device-{self.tag}")
            self._hb.start()

    def stop(self, reason: str = "stop") -> None:
        if self._hb is not None:
            self._hb.stop()
            self._hb = None
        if self.sim is not None:
            self.close()
        self.log("device_stop", reason=reason)

    # ── 租約 ────────────────────────────────────────────────────────────────
    def acquire(self, owner: str) -> bool:
        """非阻塞、一次一件。同 owner 重入只給 worker 的 `queue:<store>`（同一批續跑）。
        #! 檢查 #1（2026-09-07）：以前任何同名 owner 都可重入，而 MCP 的 owner＝`mcp:<by>`、`by` 有預設值——兩個呼叫者都不帶 by
        #  就同時拿到儀器、同機開兩個 HFSS、先跑完的關掉對方的。現在 MCP owner 一次性且永不重入。"""
        with self._lock:
            if self._owner is not None and (self._owner != owner or not owner.startswith("queue:")):
                self.log("lease_refused", owner=owner, holder=self._owner)
                return False
            self._owner = owner
            self._set(owner=owner)
            return True

    def release(self, owner: str) -> bool:
        with self._lock:
            if self._owner != owner:
                return False
            self._owner = None
            self._set(owner=None)
            return True

    def announce_url(self, url: str | None) -> None:
        """MCP server 起來後把端點寫進狀態字典（`fleet --mcp-config` 讀它）。"""
        self._set(url=url)

    # ── 兩段式確認（simulate_once／MCP 的 stop_worker 等共用） ─────────────
    def issue_confirm(self, op: str, key: str) -> str:
        """發一個 token：sha1(secret|op|key|nonce|到期)[:8]，記在 `_issued`。
        #! 檢查 #9（2026-09-07）：以前 token＝sha1(secret|op|key|10 分鐘窗)——同窗同值、用過即燒 → 同一件事十分鐘內做不了第二次，
        #  錯誤訊息還叫人「重拿」（拿回同一個）。現在每次發的都不同（nonce）、消費即失效、綁 op/key、confirm_window_s 後到期。"""
        now = self._clock()
        expires_at = now + float(self.limits.confirm_window_s)
        nonce = secrets.token_hex(4)
        token = hashlib.sha1(f"{self._secret}|{op}|{key}|{nonce}|{expires_at:.0f}".encode("utf-8")).hexdigest()[:8]
        with self._lock:
            self._issued = {t: v for t, v in self._issued.items() if v[2] >= now}      # 順手清過期
            self._issued[token] = (op, key, expires_at)
        return token

    def check_confirm(self, op: str, key: str, token: str | None) -> bool:
        """只驗不消費：發過、op/key 相符、未到期、未消費。操作真的開始再 `consume_confirm`——被拒（busy／急停）不燒 token。"""
        with self._lock:
            rec = self._issued.get(token or "")
        return rec is not None and rec[0] == op and rec[1] == key and self._clock() <= rec[2]

    def consume_confirm(self, token: str) -> None:
        """消費＝從 `_issued` 移除；之後重放即 ConfirmRejected。"""
        with self._lock:
            self._issued.pop(token, None)

    # ── 急停 ────────────────────────────────────────────────────────────────
    def estop_engaged(self) -> dict | None:
        """讀三層急停；狀態跟著走（進 estop 記 estop_engaged；解除且模擬器已關 → idle 記 estop_cleared）。"""
        info = estop.engaged(self.depot, self.root, self.tag)
        with self._lock:
            if info is not None and self.state.state != "estop":
                self._set("estop", estop=info)
                self.log("estop_engaged", scope=info["scope"], by=info.get("by"), reason=info.get("reason"))
            elif info is None and self.state.state == "estop":
                self._set("ready" if self.sim is not None else "idle", estop=None)
                self.log("estop_cleared", scope=(self.state.estop or {}).get("scope"))
        return info

    def _check_estop(self) -> None:
        info = self.estop_engaged()
        if info is not None:
            raise estop.EstopEngaged(info)

    # ── Simulator 協定直傳 ─────────────────────────────────────────────────
    def bind(self, profile, workdir, *, store: str | None = None) -> None:
        """綁定要開的 profile 與本機工作目錄（open()／重開都用它）。"""
        self._bound = (profile, Path(workdir), store)

    def unbind(self) -> None:
        self._bound = None

    def open(self) -> None:
        """單次嘗試（重試交給 `guard.open_with_retries`）：急停 → 前置檢查 → 建構 → open；失敗 → fault 並原樣拋。"""
        if self._bound is None:
            raise RuntimeError("open() 前先 bind(profile, workdir)")
        profile, workdir, store = self._bound
        self._check_estop()
        h = doctor.health(self.root, depot=self.depot)
        if self._opened_once:
            #? 冷啟動才把「ansysedt 已在跑」當阻擋（舊 worker 沒停）；這台開過一次之後，殘留的 ansysedt 是自己的（kill()／重開會收）。
            h["blocking"] = [b for b in h["blocking"] if "ansysedt" not in b]
        problems = check_preconditions(profile, limits=self.limits, depot=self.depot, root=self.root, health=h)
        if problems:
            raise PreconditionFailed(problems)
        self._set(free_gb=h.get("free_gb"), ansysedt_running=h.get("ansysedt_running"))
        self._set("opening", profile=profile.name, profile_hash=profile.profile_hash, store=store, last_error=None)
        try:
            self.sim = self._factory(workdir, profile)
            self.sim.open()
        except Exception as e:  # noqa: BLE001 — 開不起來＝fault；呼叫端決定重試或判死
            self._set("fault", last_error=f"open: {type(e).__name__}: {e}")
            self.log("device_fault", error=f"open: {type(e).__name__}: {e}")
            raise
        self._opened_once = True
        self._set("ready")
        self.refresh_reference()

    def simulate(self, bits):
        """一筆進一筆出（SimResult）；急停硬檢查；錯誤計數後原樣拋（結果檔由呼叫端 make_result／error_result）。"""
        if self.sim is None:
            raise RuntimeError("simulate() 前先 open()")
        self._check_estop()
        rid = record_id(bits, self._bound[0].name)
        self._set("busy", current_id=rid, sample_started_at=now_iso())
        t0 = time.time()
        try:
            out = self.sim.simulate(bits)
        except Exception as e:  # noqa: BLE001
            self._set("ready" if self.state.state == "busy" else None, current_id=None, sample_started_at=None,
                      n_error=self.state.n_error + 1, last_error=f"{type(e).__name__}: {e}")
            raise
        elapsed = float(out.time_s) if out.time_s else time.time() - t0
        self.recent_time_s.append(elapsed)
        del self.recent_time_s[:-RECENT_MAX]
        self._set("ready" if self.state.state == "busy" else None, current_id=None, sample_started_at=None,
                  n_done=self.state.n_done + 1, last_result_at=now_iso())
        return out

    def kill(self) -> None:
        """不經鎖、可跨執行緒（看門狗、abort）。"""
        sim = self.sim
        if sim is not None:
            sim.kill()

    def close(self) -> None:
        sim, self.sim = self.sim, None
        if sim is not None:
            try:
                sim.close()
            except Exception:  # noqa: BLE001 — 關不掉就算了（kill 已做過或呼叫端會做）
                pass
        self._set("idle" if self.state.state != "estop" else None, profile=None, profile_hash=None, store=None,
                  current_id=None, sample_started_at=None)

    # ── 程序 ────────────────────────────────────────────────────────────────
    def abort(self, *, by: str) -> None:
        """殺正在跑的那筆（＝kill）；不經租約——急停與人都能按。"""
        self.log("device_abort", by=by)
        try:
            self.kill()
        except Exception:  # noqa: BLE001
            pass

    def median_time_s(self) -> float | None:
        return float(statistics.median(self.recent_time_s)) if self.recent_time_s else None

    def selfcheck(self) -> dict:
        problems = list(self.depot.selfcheck())
        h = doctor.health(self.root, depot=self.depot)
        return {"depot": problems, "health": h, "state": self.state.state, "owner": self._owner,
                "estop": self.estop_engaged(), "problems": problems + list(h["blocking"])}

    def simulate_once(self, profile_name: str, bits, *, by: str, confirm: str | None = None) -> dict:
        """兩段式：無 token → {needs_confirm, token, record_id, preview}；帶 token → 跑一筆，結果寫 adhoc（不入 db）。"""
        profile = profiles.get_profile(profile_name)
        bits = _check_bits(profile, bits)
        rid = record_id(bits, profile.name)
        op_key = f"{profile.name}/{rid}"
        if confirm is None:
            return {"needs_confirm": True, "token": self.issue_confirm("simulate", op_key),
                    "record_id": rid, "preview": {"profile": profile.name, "shape": list(profile.shape),
                                                  "n_on": int(bits.sum()), "timeout_s": profile.timeout_s,
                                                  "estimated_s": self.median_time_s()}}
        if not self.check_confirm("simulate", op_key, confirm):
            raise ConfirmRejected(f"token {confirm!r} 不對、過期或用過（先不帶 confirm 拿新 token）")
        self._check_estop()                      # 急停：不燒 token、不搶租約
        owner = f"mcp:{by}:{secrets.token_hex(4)}"    # 一次性 owner：兩個 MCP 呼叫永遠不同名，不可能「重入」（檢查 #1）
        if not self.acquire(owner):
            raise DeviceBusy(f"儀器 {self.tag} 被 {self._owner} 持有")
        stamp = time.strftime("%Y%m%d%H%M%S")
        try:
            res = self._run_adhoc(profile, bits, rid, by=by, stamp=stamp)
        finally:
            self.close()
            self.unbind()
            self.work.remove(f"adhoc-{stamp}-{rid[:8]}")
            self.release(owner)
        self.consume_confirm(confirm)            # 真的跑了才算用掉
        return res

    def _run_adhoc(self, profile, bits, rid: str, *, by: str, stamp: str) -> dict:
        self.bind(profile, self.work.make(f"adhoc-{stamp}-{rid[:8]}"), store=None)
        #? 急停／前置檢查不過＝拒絕不是卡住：原樣拋給呼叫端（MCP 轉 estop_engaged／precondition_failed），不重試三次。
        open_with_retries(self, sleep=self._sleep, fatal=(estop.EstopEngaged, PreconditionFailed))
        base = result_base(rid, attempts=1, machine=self.tag, worker_ver=self.state.worker_ver,
                           profile_hash=profile.profile_hash)
        t0 = time.time()
        try:
            out = guarded_call(lambda: self.simulate(bits), float(profile.timeout_s), self.kill)
            res = make_result(profile, base, out, time.time() - t0)
        except Exception as e:  # noqa: BLE001 — 模擬失敗是資料（status=error），不是儀器層的錯
            res = error_result(base, f"{type(e).__name__}: {e}")
        res.update({"by": by, "adhoc": True})
        self.depot.put_json(paths.adhoc_result(self.tag, rid, stamp), res)
        self.log("device_simulate", id=rid, by=by, status=res["status"], time_s=res.get("time_s"))
        return res


def _check_bits(profile, bits) -> np.ndarray:
    b = np.asarray(bits)
    if b.dtype != bool:
        b = b > 0.5
    if b.shape != tuple(profile.shape):
        raise ValueError(f"bad_bits: shape {b.shape} ≠ profile {profile.name} 的 {tuple(profile.shape)}")
    if not b[profile.fixed_on].all():
        raise ValueError(f"bad_bits: fixed_on 像素必為 True（profile {profile.name}）")
    return b
