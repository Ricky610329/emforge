"""emforge/runtime/collect.py — 收結果：增量讀 results/<id>.json → profile_hash 比對 → 量測（凍結）→ 評分（可換）→ 入庫 → 收尾。

#! 回歸 I-10：結果自帶量它的儀器指紋；不符＝有人改了 registry／舊程式在跑 → profile_tamper、不入庫（但記為已收，不重讀）。
量測與評分只在這裡發生（策略／worker 都不算分，D1）。error 也入庫（status=error、response=None），去重不算它。
"""
import numpy as np

from .. import fs, paths, specs
from ..batches import Batch
from ..model import KIND_SAMPLE, STATUS_DONE, STATUS_ERROR, Record


def collect(rt) -> list:
    """走一遍所有 inflight；回本次新入庫的 Record。"""
    new = []
    for inf in rt.inflight():
        new.extend(_collect_one(rt, inf))
    return new


def _collect_one(rt, inf: dict) -> list:
    store = inf["store"]
    batch = Batch(rt.root, store)
    collected = set(inf["collected"])
    results = batch.results(known_ids=collected)
    new = []
    if results:
        patterns = batch.patterns()
        for rid in sorted(results):
            rec = _to_record(rt, inf, rid, results[rid], patterns)
            collected.add(rid)
            if rec is not None and rt.db.add(rec):
                rt.event("record_added", id=rid, store=store, score=rec.score, kind=rec.kind)
                new.append(rec)
        inf["collected"] = sorted(collected)
        fs.atomic_write_json(paths.inflight_file(rt.root, rt.profile_name, store), inf)
    _maybe_finalize(rt, inf, collected)
    return new


def _to_record(rt, inf: dict, rid: str, res: dict, patterns: dict):
    profile = rt.profile
    if res.get("profile_hash") != profile.profile_hash:
        rt.event("profile_tamper", store=inf["store"], expected=profile.profile_hash, got=res.get("profile_hash"))
        return None
    item = inf["items"].get(rid, {})
    note = dict(item.get("note") or {})
    run = {"store": inf["store"], "machine": res.get("machine"), "worker_ver": res.get("worker_ver"),
           "profile_hash": res.get("profile_hash"), "time_s": res.get("time_s")}
    response, measure, score, status = None, {}, None, STATUS_ERROR
    if res.get("status") == STATUS_DONE:
        try:
            response = np.asarray(res["response"], np.float32)
            measure = specs.measure(profile.measure, response, profile.labels)
            score = specs.score(profile.spec, measure)
            status = STATUS_DONE
        except Exception as e:  # noqa: BLE001 — 尺炸了是這一筆的 error，不是 runtime 的
            response, measure, score = None, {}, None
            note["error"] = f"measure_failed: {type(e).__name__}: {e}"
    else:
        note["error"] = res.get("error", "unknown")
    return Record(id=rid, sim_profile=profile.name, bits=patterns[rid], response=response, measure=measure,
                  score=score, status=status, strategy=inf["strategy"], arm=item.get("arm"), parent=item.get("parent"),
                  tick=inf["tick"], seed=inf["seed"], note=note, kind=inf["kind"], run=run,
                  extra=dict(res.get("extra") or {}))


def _maybe_finalize(rt, inf: dict, collected: set) -> None:
    """批到終態（done／fail）且能收的都收了 → 移除 inflight、發事件、算錯誤率。"""
    store = inf["store"]
    state = rt.queue.state(store)
    if state not in ("done", "fail"):
        return
    ids = set(inf["ids"])
    if state == "done" and not ids <= collected:
        return                                       # worker 標 done 但檔還沒讀齊（下個 tick 再收）
    results = Batch(rt.root, store).results()
    n_done = sum(1 for r in results.values() if r.get("status") == STATUS_DONE)
    n_error = sum(1 for r in results.values() if r.get("status") != STATUS_DONE)
    fs.release(paths.inflight_file(rt.root, rt.profile_name, store))
    if state == "done":
        rt.event("batch_done", store=store, n_done=n_done, n_error=n_error)
    else:
        rt.event("batch_failed", store=store, reason=f"worker 判死；已收 {len(collected)}/{len(ids)} 筆")
    total = n_done + n_error
    if inf["kind"] == KIND_SAMPLE and total:
        rate = n_error / total
        if rate > rt.config.runtime.max_error_rate:
            rt.state["paused_profile"] = {"store": store, "error_rate": rate, "at": fs.now_iso()}
            rt.event("profile_paused", error_rate=rate, store=store)
