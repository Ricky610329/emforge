"""emforge/worker/loop.py — worker 主迴圈：STOP → pick → gate → run_batch → mark_done／mark_fail。

啟動：載入 `<root>/registry.py`（與 runtime 同源）、印 worker_ver（I-10）、清工作目錄（I-1）、寫 worker_start。
守門不過＝那**批**判死（.fail），worker 繼續；保險絲熔斷＝那批判死，worker 也繼續（別台會接管）；
只有 STOP 檔讓 worker 收工，且在 job **之間**生效（不中斷單筆）。
"""
import time
from dataclasses import dataclass
from pathlib import Path

from .. import _version, events, paths, profiles
from ..batches import Batch
from ..queue import Queue
from .batch import run_batch
from .fuse import Fuse
from .gate import gate
from .workdir import WorkDir, default_work_root


def worker_version() -> str:
    """worker_ver：emforge 的 git 戳；adapter 可再附自己的（見 adapters/）。"""
    return _version.describe()


@dataclass
class _Opts:
    background_prio: int = 9
    max_fail: int = 5
    cooldown_s: float = 600.0
    max_blowout: int = 3
    retry_passes: int = 2


def worker_loop(root, machine_tag: str, *, poll_s: float = 30.0, once: bool = False, work_root=None,
                sleep=time.sleep, sim_factory=None, background_prio: int = 9, max_fail: int = 5,
                cooldown_s: float = 600.0, max_blowout: int = 3, retry_passes: int = 2) -> int:
    """回 0＝正常收工。`once=True`：跑完第一個真正執行的 job（或佇列空）就回。
    `sim_factory(workdir, profile)` 可注入（測試）；預設 `profiles.make_simulator`。"""
    root = Path(root)
    opts = _Opts(background_prio, max_fail, cooldown_s, max_blowout, retry_passes)
    work = WorkDir(work_root or default_work_root())
    log_path = paths.worker_log(root, machine_tag)

    def log(name, **fields):
        events.emit(log_path, name, **fields)

    profiles.load_user_registry(root)
    ver = worker_version()
    print(f"emforge worker {ver} machine={machine_tag} root={root}", flush=True)
    swept = work.sweep_all()
    if swept:
        print(f"啟動清掃：{len(swept)} 個殘留工作目錄已刪（{work.root}）", flush=True)
    log("worker_start", worker_ver=ver, machine=machine_tag)
    q = Queue(root)
    while True:
        if q.stop_requested(machine_tag):
            log("worker_stop", reason="stop_file")
            return 0
        job = q.pick(machine_tag)
        if job is None:
            if once:
                log("worker_stop", reason="once")
                return 0
            sleep(poll_s)
            continue
        log("job_claimed", store=job.store, prio=job.prio)
        ran = _handle(q, root, job, work, log, ver, machine_tag, sim_factory, sleep, opts)
        if once and ran:
            log("worker_stop", reason="once")
            return 0


def _handle(q, root, job, work, log, ver, machine_tag, sim_factory, sleep, opts: _Opts) -> bool:
    """守門 → run_batch → 終態。回 True＝真的跑了 run_batch（done／fail／yield）。"""
    verdict = gate(job, root)
    if not verdict.ok:
        log("gate_rejected", store=job.store, reason=verdict.reason)
        q.mark_fail(job.store, machine_tag, verdict.reason)
        return False
    profile = verdict.profile
    if sim_factory is not None:
        factory = lambda wd: sim_factory(wd, profile)          # noqa: E731
    else:
        factory = lambda wd: profiles.make_simulator(profile, wd)   # noqa: E731
    outcome = run_batch(q, Batch(root, job.store), job, profile, factory, machine_tag, ver, work=work,
                        fuse=Fuse(opts.max_fail, opts.cooldown_s, opts.max_blowout), retry_passes=opts.retry_passes,
                        background_prio=opts.background_prio, sleep=sleep, log=log)
    if outcome == "done":
        res = Batch(root, job.store).results()
        errs = sorted(i for i, r in res.items() if r.get("status") != "done")
        q.mark_done(job.store, machine_tag, n_done=len(res) - len(errs), n_error=len(errs), error_ids=errs)
        log("job_done", store=job.store, n_done=len(res) - len(errs), n_error=len(errs))
    elif outcome == "fail":
        q.mark_fail(job.store, machine_tag, "run_batch failed（見 worker log 的 job_failed）")
    return True
