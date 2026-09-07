"""emforge/runtime/reconcile.py — 啟動對帳：inflight／佇列／批 三方一致才准 tick。

#! 回歸 I-14（2026-07-13）：在未落地的狀態上又疊了三層工作。不一致就停下印差異，不繼續。
只看本 profile、origin=runtime 的 job；別的實例、cli:smoke 之類不歸這個 runtime 管。
"""
from .. import paths


def reconcile(rt) -> list:
    """回問題清單；空＝一致。"""
    problems = []
    inflight = {i["store"] for i in rt.inflight()}
    jobs = {j.store: j for j in rt.queue.list()}
    for store in sorted(inflight):
        if store not in jobs:
            problems.append(f"inflight_without_job: {store}")
        if not (rt.depot.exists(paths.batch_manifest(store)) and rt.depot.exists(paths.batch_patterns(store))):
            problems.append(f"inflight_without_batch: {store}")
    for store, job in sorted(jobs.items()):
        if job.sim_profile != rt.profile_name or job.origin not in ("runtime", "inbox") or store in inflight:
            continue
        if rt.queue.state(store) in ("done", "fail"):
            continue                                 # 終態且已收尾（inflight 在 collect 收尾時移除）
        problems.append(f"job_without_inflight: {store}")
    return problems
