"""emforge/worker/loop.py — worker 主迴圈：STOP → 急停 → pick → gate → 儀器租約 → run_batch → mark_done／mark_fail。

啟動：載入 registry、印版本、取得工作目錄所有權並啟動儀器，再取得儀器租約清理暫存、寫 worker_start。
守門不過＝那**批**判死（.fail），worker 繼續；保險絲熔斷＝那批判死，worker 也繼續（別台會接管）；
一圈裡的 depot 例外（SMB 瞬斷）記 worker_error 續跑、連續 WORKER_ERROR_LIMIT 圈才收工（檢查 #7）；
只有 STOP 檔讓 worker 收工，且在 job **之間**生效（不中斷單筆）。
M13：worker 經 `Instrument` 跑批——急停中 job 之間不撿（等 poll_s 再看）；儀器租約被 MCP 持有 → 放掉 claim、下一輪再撿。
"""
import time
from dataclasses import dataclass
from pathlib import Path

from .. import _version, events, paths, profiles
from ..batches import Batch
from ..depot import FileDepot, open_depot
from ..device.instrument import Instrument
from ..queue import Queue
from .batch import run_batch
from .fuse import Fuse
from .gate import gate
from .workdir import WorkDir, default_work_root
from .outbox import ResultOutbox, ResultPending


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


def make_instrument(root, machine_tag: str, *, depot=None, work_root=None, sim_factory=None, sleep=time.sleep,
                    worker_ver: str | None = None) -> Instrument:
    """worker 迴圈與 `worker --serve`／`device-serve` 用同一種建法（同機只能一台）。不 start——呼叫端管生命週期。"""
    root = Path(root)
    depot = open_depot(depot) if depot is not None else FileDepot(root)
    profiles.load_user_registry(root)
    factory = sim_factory or (lambda wd, p: profiles.make_simulator(p, wd))
    return Instrument(root, machine_tag, depot=depot, sim_factory=factory, worker_ver=worker_ver or worker_version(),
                      work_root=work_root or default_work_root(), sleep=sleep)


def worker_loop(root, machine_tag: str, *, depot=None, poll_s: float = 30.0, once: bool = False, work_root=None,
                sleep=time.sleep, sim_factory=None, background_prio: int = 9, max_fail: int = 5,
                cooldown_s: float = 600.0, max_blowout: int = 3, retry_passes: int = 2, instrument=None) -> int:
    """回 0＝正常收工。`once=True`：跑完第一個真正執行的 job（或佇列空）就回。
    `root`＝本機程式碼根（registry.py）；`depot`＝共享協調狀態（預設 FileDepot(root)）。
    `sim_factory(workdir, profile)` 可注入（測試）；預設 `profiles.make_simulator`。
    `instrument` 可注入（MCP server 與迴圈共用同一台）；不給就自建並在收工時 stop。"""
    root = Path(root)
    depot = open_depot(depot) if depot is not None else FileDepot(root)
    opts = _Opts(background_prio, max_fail, cooldown_s, max_blowout, retry_passes)
    work = WorkDir(work_root or default_work_root())
    log_key = paths.worker_log(machine_tag)

    def log(event, /, **fields):
        events.emit(depot, log_key, event, **fields)

    profiles.load_user_registry(root)
    ver = worker_version()
    print(f"emforge worker {ver} machine={machine_tag} root={root} depot={depot.spec}", flush=True)
    inst = instrument or make_instrument(root, machine_tag, depot=depot, work_root=work.root, sim_factory=sim_factory,
                                         sleep=sleep, worker_ver=ver)
    if work_root is not None and inst.work.root != work.root:
        raise ValueError("worker 與儀器的工作目錄必須相同")
    work, owned = inst.work, not inst.started
    inst.start()
    try:
        _sweep(inst, work)
        log("worker_start", worker_ver=ver, machine=machine_tag)
        return _loop(Queue(depot), inst, work, log, ver, machine_tag, sleep, poll_s, once, opts)
    finally:
        if owned:
            inst.stop()


def _sweep(inst, work):
    """已有同一行程 MCP 工作時跳過清掃，等待正常儀器租約。"""
    if not inst.acquire("startup"):
        return
    try:
        swept = work.sweep_all()
        if swept:
            print(f"啟動清掃：{len(swept)} 個殘留工作目錄已刪（{work.root}）", flush=True)
    finally:
        inst.release("startup")


WORKER_ERROR_LIMIT = 10   #? 連續幾圈例外才收工：一次 SMB 瞬斷（OSError 64／59／1231、FsBusy）不能殺整夜的 worker（檢查 #7）


def _loop(q, inst, work, log, ver, machine_tag, sleep, poll_s, once, opts) -> int:
    """外圈＝故障邊界。以前只有 `_handle`（gate 之後）有 try——pick／log／release 的 depot 例外直接穿出、行程結束，
    而 start_worker.cmd 沒有重啟迴圈。現在一圈裡任何例外：記 worker_error、睡一輪續跑；連續 WORKER_ERROR_LIMIT 圈才回 1。
    KeyboardInterrupt／SystemExit 不吞。"""
    errors, st = 0, {"estop_waited": False}
    while True:
        try:
            rc = _iteration(q, inst, work, log, ver, machine_tag, sleep, poll_s, once, opts, st)
        except Exception as e:  # noqa: BLE001
            errors += 1
            _log_quiet(log, "worker_error", error=f"{type(e).__name__}: {e}", consecutive=errors)
            if errors >= WORKER_ERROR_LIMIT:
                _log_quiet(log, "worker_stop", reason="worker_error")
                return 1
            sleep(poll_s)
            continue
        errors = 0
        if rc is not None:
            return rc


def _iteration(q, inst, work, log, ver, machine_tag, sleep, poll_s, once, opts, st) -> int | None:
    """一圈：STOP → 急停 → pick → handle。回 None＝繼續；int＝收工碼。"""
    if q.stop_requested(machine_tag):
        log("worker_stop", reason="stop_file")
        return 0
    ResultOutbox(work.local, q.depot, machine_tag).flush(q)
    if inst.estop_engaged() is not None:
        if once and st["estop_waited"]:
            log("worker_stop", reason="estop")                # 檢查 #15：--once 給一個 poll 的寬限，仍急停就收工（以前無限空轉）
            return 0
        st["estop_waited"] = True
        sleep(poll_s)                                         # 急停中：job 之間不撿
        return None
    job = q.pick(machine_tag)
    if job is None:
        if once:
            log("worker_stop", reason="once")
            return 0
        sleep(poll_s)
        return None
    log("job_claimed", store=job.store, prio=job.prio)
    ran = _handle(q, inst, job, work, log, ver, machine_tag, sleep, opts)
    if ran is None:
        sleep(poll_s)                                         # 儀器被 MCP 持有：claim 放掉了，等一輪
        return None
    if once and ran:
        log("worker_stop", reason="once")
        return 0
    return None


def _handle(q, inst, job, work, log, ver, machine_tag, sleep, opts: _Opts):
    """守門 → 儀器租約 → run_batch → 終態。回 True＝真的跑了 run_batch（done／fail／yield）、False＝守門擋下、None＝儀器忙。"""
    verdict = gate(job, q.depot)
    if not verdict.ok:
        log("gate_rejected", store=job.store, reason=verdict.reason)
        q.mark_fail(job.store, machine_tag, verdict.reason)
        return False
    owner = f"queue:{job.store}"
    if not inst.acquire(owner):
        q.release(job.store, machine_tag)
        log("job_yield", store=job.store, reason="device_busy")
        return None
    try:
        outcome = run_batch(q, Batch(q.depot, job.store), job, verdict.profile, None, machine_tag, ver, work=work,
                            instrument=inst, fuse=Fuse(opts.max_fail, opts.cooldown_s, opts.max_blowout),
                            retry_passes=opts.retry_passes, background_prio=opts.background_prio, sleep=sleep, log=log)
        if outcome == "done":
            res = Batch(q.depot, job.store).results()
            errs = sorted(i for i, r in res.items() if r.get("status") != "done")
            q.mark_done(job.store, machine_tag, n_done=len(res) - len(errs), n_error=len(errs), error_ids=errs)
            log("job_done", store=job.store, n_done=len(res) - len(errs), n_error=len(errs))
        elif outcome == "fail":
            q.mark_fail(job.store, machine_tag, "run_batch failed（見 worker log 的 job_failed）")
    except ResultPending as e:
        _log_quiet(log, "job_yield", store=job.store, reason=str(e))
        return None
    except Exception as e:  # noqa: BLE001
        #! 回歸 review-6：NAS 的 FsBusy／PermissionError／FileNotFoundError 以前直接殺掉整個 worker、claim 留著 45 分沒人接。
        #  這批判死（.fail 記原因）、worker 繼續；判死本身也失敗就只能靠 stale 接管。
        reason = f"worker_exception: {type(e).__name__}: {e}"
        _log_quiet(log, "job_failed", store=job.store, reason=reason)
        try:
            q.mark_fail(job.store, machine_tag, reason)
        except Exception:  # noqa: BLE001
            pass
        work.remove(job.store)
    finally:
        inst.release(owner)
    return True


def _log_quiet(log, event, /, **fields) -> None:
    try:
        log(event, **fields)
    except Exception:  # noqa: BLE001 — 連 log 檔都寫不進去也不能讓 worker 死
        pass
