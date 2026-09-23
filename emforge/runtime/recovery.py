"""只補完可證明的派工意圖；既有不一致不覆蓋。

#! 回歸 I-20（2026-09-23）：以前只在啟動時 `recover` 一次；執行中派工半途失敗（inflight 有、佇列沒有）會占住 max_inflight、
#  讓 fleet_quiet 停掉排程，只有重啟才修得好。現在每 tick `recover_missing` 補完佇列狀態為 missing 的意圖。
"""
import numpy as np
from .. import paths
from ..batches import Batch
from ..model import Job, record_id

RECOVER_ERROR = "recover_error"


def recover_missing(rt) -> list:
    """執行中：佇列裡沒有 job 的 inflight（派工半途死掉）→ 補完意圖、發 dispatch_recovered；回補完的 store。
    身分不一致（確定性）只報一次（旗標寫進 inflight）；瞬斷（其他例外）記 dispatch_failed、下 tick 再試。"""
    done = []
    for inf in rt.inflight():
        store = inf["store"]
        if "intent" not in inf or inf.get(RECOVER_ERROR) or rt.queue.state(store) != "missing":
            continue
        try:
            _finish(rt, inf)
        except (ValueError, KeyError, TypeError) as e:
            inf[RECOVER_ERROR] = f"{type(e).__name__}: {e}"
            rt.depot.put_json(paths.inflight_file(rt.profile_name, store), inf)
            rt.event("dispatch_failed", name=inf.get("strategy", "?"), tick=rt.state["tick"], error=f"recover: {e}")
        except Exception as e:  # noqa: BLE001
            rt.event("dispatch_failed", name=inf.get("strategy", "?"), tick=rt.state["tick"], error=f"recover: {type(e).__name__}: {e}")
        else:
            rt.event("dispatch_recovered", store=store)
            done.append(store)
    return done


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
    jobs = {j.store: j for j in rt.queue.list(original=True)}
    if store in jobs and jobs[store].to_dict() != job.to_dict():
        raise ValueError("現有 job 不同")
    if batch.exists() and not rt.depot.exists(paths.batch_patterns(store)):
        raise ValueError("已發布批遺失 patterns，拒絕覆寫")
    if not batch.exists():
        batch.write(manifest, pats, ids)
    if store not in jobs:
        rt.queue.add(job)
