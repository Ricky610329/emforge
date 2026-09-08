"""emforge/worker/batch.py — `run_batch`：一批的逐筆迴圈。只組裝 guard／fuse／workdir／儀器，不認得領域。

流程：建工作目錄 → 建＋開模擬器（三試）→ 第 0 輪跑「還沒 done 的」（續跑）→ 補測輪跑「error 且 attempts<3 的」
（每輪前殺透重開：kill→close→open）→ 每筆：看門狗下 simulate → 逐筆結果檔 → 保險絲 → 讓位檢查 → 結束一律關模擬器（帶處決線）、刪工作目錄。
回傳 "done"（跑完；殘留 error 記在結果檔）／"yield"（claim 被搶、讓位給前景、急停）／"fail"（熔斷或開不起來）。
結果檔只有原始響應與戳記；量測與評分是 runtime 的事。

M13：給 `instrument`（`device.Instrument`）就經儀器跑——Instrument 實作 Simulator 協定（open/simulate/kill/close），
所以逐筆邏輯不變；多的是：每筆前急停讓位（`job_yield reason=estop_engaged`）、正在跑的那筆被急停 `abort`（吃一次 attempts、不重開）。
不給 instrument（測試／舊呼叫）就直接用 `sim_factory(wd)`。
"""
import time
from dataclasses import dataclass

from ..batches import error_result, make_result, result_base
from ..device.estop import EstopEngaged
from ..device.instrument import PreconditionFailed
from .fuse import Fuse
from . import guard
from .guard import Aborted, SimulatorOpenFailed, WatchdogTimeout, guarded_call, open_with_retries
from .workdir import WorkDir

MAX_ATTEMPTS = 3   #? 毒樣本規則：三次都錯就不再重試，留給人判
DEFAULT_ESTOP_POLL_S = 5.0
#! 檢查 #6（2026-09-07）：急停／前置檢查不過是「拒絕」不是「機器卡住」——以前被 open_with_retries 當卡住重試 3×15 s、
#  SimulatorOpenFailed → .fail（急停解除後這台永遠不再撿這批）。原樣拋：急停 → 讓位；前置不過 → 判死但指名原因。
OPEN_FATAL = (EstopEngaged, PreconditionFailed)


def _noop(event, /, **fields):
    return None


@dataclass
class _Run:
    queue: object
    batch: object
    job: object
    profile: object
    machine_tag: str
    worker_ver: str
    timeout_s: float
    fuse: Fuse
    background_prio: int
    sleep: object
    log: object
    instrument: object = None
    estop_poll_s: float = DEFAULT_ESTOP_POLL_S
    sim: object = None

    def estop(self) -> bool:
        return self.instrument is not None and self.instrument.estop_engaged() is not None


def run_batch(queue, batch, job, profile, sim_factory, machine_tag: str, worker_ver: str, *, work: WorkDir,
              instrument=None, timeout_s: float | None = None, fuse: Fuse | None = None, retry_passes: int = 2,
              background_prio: int = 9, sleep=time.sleep, log=_noop, estop_poll_s: float = DEFAULT_ESTOP_POLL_S) -> str:
    run = _Run(queue, batch, job, profile, machine_tag, worker_ver, float(timeout_s or profile.timeout_s),
               fuse or Fuse(), background_prio, sleep, log, instrument, float(estop_poll_s))
    wd = work.make(job.store)
    try:
        if instrument is not None:
            instrument.bind(profile, wd, store=job.store)
            run.sim = instrument
        else:
            run.sim = sim_factory(wd)
        open_with_retries(run.sim, sleep=sleep, fatal=OPEN_FATAL)
        patterns = batch.patterns()
        for rpass in range(1 + retry_passes):
            todo = _todo(batch, rpass)
            if not todo:
                if rpass == 0:
                    continue
                break
            if rpass:
                _restart(run, "retry_pass")
            outcome = _run_pass(run, patterns, todo, rpass)
            if outcome != "continue":
                return outcome
        return "done"
    except EstopEngaged:
        return _yield(run, "estop_engaged")            # pick 之後、open 之前按了急停：讓位，不進 .fail
    except PreconditionFailed as e:
        log("job_failed", store=job.store, reason=f"precondition_failed: {e}")
        return "fail"
    except SimulatorOpenFailed as e:
        log("job_failed", store=job.store, reason=f"simulator_open_failed: {e}")
        return "fail"
    finally:
        if run.sim is not None:
            _close_quiet(run.sim)
        if instrument is not None:
            instrument.unbind()
        work.remove(job.store)


def _todo(batch, rpass: int) -> list:
    """第 0 輪：還沒 done 且 attempts < MAX_ATTEMPTS 的（續跑）；補測輪：error 且 attempts < MAX_ATTEMPTS。回 [(id, 之前 attempts)]。
    #! 回歸 review-10：第 0 輪以前不看 attempts——批被讓位／接管／requeue 重進時毒樣本第 4、5、6 次再跑，每台都吃一次逾時＋重開。"""
    res = batch.results()
    if rpass == 0:
        return [(i, res.get(i, {}).get("attempts", 0)) for i in batch.ids()
                if res.get(i, {}).get("status") != "done" and res.get(i, {}).get("attempts", 0) < MAX_ATTEMPTS]
    return [(i, res[i]["attempts"]) for i in batch.ids()
            if res.get(i, {}).get("status") == "error" and res[i].get("attempts", 1) < MAX_ATTEMPTS]


def _run_pass(run: _Run, patterns: dict, todo: list, rpass: int) -> str:
    consecutive = 0
    for rid, prev in todo:
        if run.estop():
            return _yield(run, "estop_engaged")            # 每筆前：急停就讓位（不動這筆）
        res = _simulate_one(run, rid, patterns[rid], prev + 1)
        run.batch.write_result(rid, res)
        run.queue.touch_claim(run.job.store)            # claim 心跳：requeue／接管都看得到「還活著」
        if res["status"] == "error":
            run.log("sample_error", store=run.job.store, id=rid, error=res["error"], attempts=res["attempts"])
            consecutive += 1
            if res["error"].startswith("aborted:"):
                return _yield(run, "estop_engaged")        # 正在跑的那筆被急停殺掉：吃一次 attempts、不重開、不算保險絲
            if res["error"].startswith("watchdog_timeout"):
                _restart(run, "watchdog_timeout")
            if rpass == 0:
                verdict = run.fuse.failure()
                if verdict == "blown":
                    run.log("job_failed", store=run.job.store, reason="fuse_blown")
                    return "fail"
                if verdict == "cooldown":
                    run.sleep(run.fuse.cooldown_s)
                    _restart(run, "fuse_cooldown")
            elif consecutive >= MAX_ATTEMPTS:
                return "continue"                    # 補測輪連三敗：放棄本輪，不燒了
        else:
            run.log("sample_done", store=run.job.store, id=rid, time_s=res["time_s"])
            consecutive = 0
            run.fuse.success()
        reason = _yield_reason(run)
        if reason:
            run.log("job_yield", store=run.job.store, reason=reason)
            return "yield"
    return "continue"


def _simulate_one(run: _Run, rid: str, bits, attempts: int) -> dict:
    base = result_base(rid, attempts=attempts, machine=run.machine_tag, worker_ver=run.worker_ver,
                       profile_hash=run.job.profile_hash)
    t0 = time.time()
    abort_if = run.estop if run.instrument is not None else None
    try:
        out = guarded_call(lambda: run.sim.simulate(bits), run.timeout_s, run.sim.kill,
                           abort_if=abort_if, poll_s=run.estop_poll_s)
    except Aborted as e:
        return error_result(base, f"aborted: estop_engaged: {e}")
    except WatchdogTimeout as e:
        return error_result(base, f"watchdog_timeout: {e}")
    except Exception as e:  # noqa: BLE001 — 模擬器的任何錯都是這一筆的 error，不是 worker 的
        return error_result(base, f"{type(e).__name__}: {e}")
    return make_result(run.profile, base, out, time.time() - t0)


def _yield(run: _Run, reason: str) -> str:
    """讓位：放掉**自己的** claim（急停可能只停這台，別台可續跑）、記 job_yield。"""
    run.queue.release(run.job.store, run.machine_tag)
    run.log("job_yield", store=run.job.store, reason=reason)
    return "yield"


def _yield_reason(run: _Run) -> str | None:
    """每筆之後：claim 被別台接走 → 停寫退出（不動別人的 claim）；背景 job 遇前景出現 → 釋放 claim 讓位；急停 → 釋放讓位。"""
    if run.queue.claim_owner(run.job.store) != run.machine_tag:
        return "claim_taken_over"
    if _todo(run.batch, 0) and run.queue.should_yield(run.job, run.machine_tag):
        run.queue.release(run.job.store, run.machine_tag)
        return "foreground_job_appeared" if run.job.prio >= run.background_prio else "priority_or_fair_turn"
    if run.estop():
        run.queue.release(run.job.store, run.machine_tag)
        return "estop_engaged"
    return None


def _restart(run: _Run, reason: str) -> None:
    """殺透重開：kill → close → open。
    #! 檢查 #17（2026-09-07）：以前 close→kill——經 Instrument 時 close() 先把 sim 設 None，kill 打空（殘留 ansysedt 沒人收）。"""
    run.log("sim_restart", store=run.job.store, reason=reason)
    try:
        run.sim.kill()
    except Exception:  # noqa: BLE001
        pass
    _close_quiet(run.sim)
    open_with_retries(run.sim, sleep=run.sleep, fatal=OPEN_FATAL)


def _close_quiet(sim) -> None:
    guard.close_quiet(sim)          # 帶處決線（檢查 #18）
