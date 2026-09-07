"""只補完可證明的派工意圖；既有不一致不覆蓋。"""
import numpy as np
from .. import paths
from ..batches import Batch
from ..model import Job, record_id

def recover(rt):
    problems = []
    for inf in rt.inflight():
        if "intent" not in inf:
            continue  # 舊格式缺乏足夠資訊，由 reconcile 拒絕缺件
        try:
            _finish(rt, inf)
        except (ValueError, KeyError, TypeError) as e:
            problems.append(f"dispatch_intent_mismatch: {inf['store']}: {e}")
    return problems

def _finish(rt, inf):
    intent, store = inf["intent"], inf["store"]
    manifest, job = intent["manifest"], Job.from_dict(intent["job"])
    pats = np.asarray(intent["patterns"])
    ids = inf["ids"]
    if manifest["store"] != store or job.store != store or manifest["profile_hash"] != rt.profile.profile_hash:
        raise ValueError("批身分不一致")
    if (manifest["sim_profile"] != rt.profile_name or job.sim_profile != rt.profile_name
            or job.profile_hash != rt.profile.profile_hash or job.n != len(ids)):
        raise ValueError("job 與 profile 不一致")
    if pats.shape != (len(ids), *rt.profile.shape) or not np.isin(pats, [0, 1]).all():
        raise ValueError("pattern 格式不一致")
    if [record_id(p, rt.profile_name) for p in pats] != ids or [i["id"] for i in manifest["items"]] != ids:
        raise ValueError("pattern id 不一致")
    batch = Batch(rt.depot, store)
    if batch.exists() and batch.manifest() != manifest:
        raise ValueError("現有 manifest 不同")
    if rt.depot.exists(paths.batch_patterns(store)):
        old = batch.patterns()
        if list(old) != ids or any(not np.array_equal(old[rid], p) for rid, p in zip(ids, pats)):
            raise ValueError("現有 patterns 不同")
    jobs = {j.store: j for j in rt.queue.list()}
    if store in jobs and jobs[store].to_dict() != job.to_dict():
        raise ValueError("現有 job 不同")
    if batch.exists() and not rt.depot.exists(paths.batch_patterns(store)):
        raise ValueError("已發布批遺失 patterns，拒絕覆寫")
    if not batch.exists():
        batch.write(manifest, pats, ids)
    if store not in jobs:
        rt.queue.add(job)
