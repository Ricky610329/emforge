"""emforge/worker/batch.py — `run_batch`：一批的逐筆迴圈。只組裝 guard／fuse／workdir，不認得領域。

流程：建工作目錄 → 建＋開模擬器（三試）→ 第 0 輪跑「還沒 done 的」（續跑）→ 補測輪跑「error 且 attempts<3 的」
（每輪前殺透重開）→ 每筆：看門狗下 simulate → 逐筆結果檔 → 保險絲 → 讓位檢查 → 結束一律關模擬器、刪工作目錄。
回傳 "done"（跑完；殘留 error 記在結果檔）／"yield"（claim 被搶或讓位給前景）／"fail"（熔斷或開不起來）。
結果檔只有原始響應與戳記；量測與評分是 runtime 的事。
"""
import time
from dataclasses import dataclass

import numpy as np

from .. import fs
from .fuse import Fuse
from .guard import SimulatorOpenFailed, WatchdogTimeout, guarded_call, open_with_retries
from .workdir import WorkDir

MAX_ATTEMPTS = 3   #? 毒樣本規則：三次都錯就不再重試，留給人判


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
    sim: object = None


def run_batch(queue, batch, job, profile, sim_factory, machine_tag: str, worker_ver: str, *, work: WorkDir,
              timeout_s: float | None = None, fuse: Fuse | None = None, retry_passes: int = 2,
              background_prio: int = 9, sleep=time.sleep, log=_noop) -> str:
    run = _Run(queue, batch, job, profile, machine_tag, worker_ver, float(timeout_s or profile.timeout_s),
               fuse or Fuse(), background_prio, sleep, log)
    wd = work.make(job.store)
    try:
        run.sim = sim_factory(wd)
        open_with_retries(run.sim, sleep=sleep)
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
    except SimulatorOpenFailed as e:
        log("job_failed", store=job.store, reason=f"simulator_open_failed: {e}")
        return "fail"
    finally:
        if run.sim is not None:
            _close_quiet(run.sim)
        work.remove(job.store)


def _todo(batch, rpass: int) -> list:
    """第 0 輪：還沒 done 的（含之前的 error，續跑）；補測輪：error 且 attempts < MAX_ATTEMPTS。回 [(id, 之前 attempts)]。"""
    res = batch.results()
    if rpass == 0:
        return [(i, res.get(i, {}).get("attempts", 0)) for i in batch.ids() if res.get(i, {}).get("status") != "done"]
    return [(i, res[i]["attempts"]) for i in batch.ids()
            if res.get(i, {}).get("status") == "error" and res[i].get("attempts", 1) < MAX_ATTEMPTS]


def _run_pass(run: _Run, patterns: dict, todo: list, rpass: int) -> str:
    consecutive = 0
    for rid, prev in todo:
        res = _simulate_one(run, rid, patterns[rid], prev + 1)
        run.batch.write_result(rid, res)
        if res["status"] == "error":
            run.log("sample_error", store=run.job.store, id=rid, error=res["error"], attempts=res["attempts"])
            consecutive += 1
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
    base = {"id": rid, "attempts": attempts, "machine": run.machine_tag, "worker_ver": run.worker_ver,
            "profile_hash": run.job.profile_hash, "at": fs.now_iso()}
    t0 = time.time()
    try:
        out = guarded_call(lambda: run.sim.simulate(bits), run.timeout_s, run.sim.kill)
    except WatchdogTimeout as e:
        return {**base, "status": "error", "error": f"watchdog_timeout: {e}"}
    except Exception as e:  # noqa: BLE001 — 模擬器的任何錯都是這一筆的 error，不是 worker 的
        return {**base, "status": "error", "error": f"{type(e).__name__}: {e}"}
    resp = np.asarray(out.response, np.float32)
    expected = (len(run.profile.labels), run.profile.n_points)
    if resp.shape != expected:
        return {**base, "status": "error", "error": f"bad_response_shape: {resp.shape} ≠ {expected}"}
    time_s = float(out.time_s) if out.time_s else time.time() - t0
    return {**base, "status": "done", "response": resp.tolist(), "time_s": time_s, "extra": dict(out.extra or {})}


def _yield_reason(run: _Run) -> str | None:
    """每筆之後：claim 被別台接走 → 停寫退出（不動別人的 claim）；背景 job 遇前景出現 → 釋放 claim 讓位。"""
    if run.queue.claim_owner(run.job.store) != run.machine_tag:
        return "claim_taken_over"
    if run.job.prio >= run.background_prio and run.queue.has_unclaimed_foreground(run.background_prio):
        run.queue.release(run.job.store)
        return "foreground_job_appeared"
    return None


def _restart(run: _Run, reason: str) -> None:
    run.log("sim_restart", store=run.job.store, reason=reason)
    _close_quiet(run.sim)
    try:
        run.sim.kill()
    except Exception:  # noqa: BLE001
        pass
    open_with_retries(run.sim, sleep=run.sleep)


def _close_quiet(sim) -> None:
    try:
        sim.close()
    except Exception:  # noqa: BLE001 — 關不掉就殺，殺不掉也不能讓收尾炸
        pass
