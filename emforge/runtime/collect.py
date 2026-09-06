"""emforge/runtime/collect.py — 收結果：增量讀 results/<id>.json → profile_hash 比對 → 量測（凍結）→ 評分（可換）→ 入庫 → 收尾。

#! 回歸 I-10：結果自帶量它的儀器指紋；不符＝有人改了 registry／舊程式在跑 → profile_tamper、不入庫（但記為已收，不重讀）。
#! 回歸 review-1：error 結果在批未 done 前**不**入庫、**不**記 collected——worker 的補測輪可能翻案（同一個結果檔被覆寫成 done），
#  記了就永遠不再讀、翻案的 HFSS 結果白燒。批到 done 終態才把殘留 error 收進 db。
#! 回歸 review-2：`.fail` 不是終態——名單外的機器會接管重跑。fail 只發一次 batch_failed、inflight 留著繼續收；
#  真的沒人接由人 `emforge abandon`（本檔 `abandon`）宣告放棄。
#! 回歸 review-8：每筆入庫後**立刻**落地 collected；上次死在「add 成功、collected 沒落地」之間的那筆，重讀時 add 回 False
#  也算新收（交給 notarize），不能靜默漏掉破榜設計。
量測與評分只在這裡發生（策略／worker 都不算分，D1）。
"""
import numpy as np

from .. import paths, specs
from ..batches import Batch
from ..model import KIND_SAMPLE, STATUS_DONE, STATUS_ERROR, Record, now_iso
from ..queue import DEFAULT_STALE_S


def collect(rt) -> list:
    """走一遍所有 inflight；回本次新收到的 Record（含已在庫但沒記到 collected 的）。"""
    new = []
    for inf in rt.inflight():
        new.extend(_collect_one(rt, inf))
    return new


def _collect_one(rt, inf: dict) -> list:
    store = inf["store"]
    batch = Batch(rt.depot, store)
    qstate = rt.queue.state(store)
    terminal = qstate == "done"
    collected = set(inf["collected"])
    results = batch.results(known_ids=collected)
    new, patterns = [], None
    for rid in sorted(results):
        res = results[rid]
        if res.get("status") != STATUS_DONE and not terminal:
            continue                            # error 而批還在跑：等補測輪翻案或終態（review-1）
        patterns = batch.patterns() if patterns is None else patterns
        rec = _to_record(rt, inf, rid, res, patterns)
        collected.add(rid)
        inf["collected"] = sorted(collected)
        if rec is not None:
            if rt.db.add(rec):
                rt.event("record_added", id=rid, store=store, score=rec.score, kind=rec.kind)
            new.append(rec)                     # add 回 False＝上次死在落地前，仍算新收（review-8）
        _persist_inflight(rt, inf)
    if qstate == "fail" and not inf.get("fail_reported"):
        inf["fail_reported"] = True
        _persist_inflight(rt, inf)
        rt.event("batch_failed", store=store,
                 reason=f"worker 判死（名單外機器可接管，inflight 保留；沒人接就 abandon）；已收 {len(collected)}/{len(inf['ids'])} 筆")
    if terminal:
        _finalize(rt, inf, collected)
    return new


def _persist_inflight(rt, inf: dict) -> None:
    key = paths.inflight_file(rt.profile_name, inf["store"])
    if rt.depot.exists(key):                    # 被 abandon 拿掉的不復活
        rt.depot.put_json(key, inf)


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


def _counts(batch: Batch) -> tuple:
    results = batch.results()
    n_done = sum(1 for r in results.values() if r.get("status") == STATUS_DONE)
    return n_done, len(results) - n_done


def _finalize(rt, inf: dict, collected: set) -> None:
    """批 done 且能收的都收了 → 移除 inflight、發事件、算錯誤率。"""
    store = inf["store"]
    if not set(inf["ids"]) <= collected:
        return                                  # worker 標 done 但檔還沒讀齊（下個 tick 再收）
    n_done, n_error = _counts(Batch(rt.depot, store))
    rt.depot.delete(paths.inflight_file(rt.profile_name, store))
    rt.event("batch_done", store=store, n_done=n_done, n_error=n_error)
    total = n_done + n_error
    if inf["kind"] == KIND_SAMPLE and total:
        rate = n_error / total
        if rate > rt.config.runtime.max_error_rate:
            rt.state["paused_profile"] = {"store": store, "error_rate": rate, "at": now_iso()}
            rt.event("profile_paused", error_rate=rate, store=store)


def abandon(rt, store: str, *, by: str) -> dict:
    """人宣告放棄一批（fail 沒人接、或不想再等）：殘留結果（含 error）收進 db、移除 inflight、佇列標 done（別台不再接管）。
    有新鮮 claim（有人正在跑）→ 拒。"""
    key = paths.inflight_file(rt.profile_name, store)
    inf = rt.depot.get_json(key)
    if inf is None:
        raise ValueError(f"{store} 不在 {rt.profile_name} 的 inflight")
    if rt.queue.state(store) == "claimed" and not rt.depot.is_stale(paths.claim_file(store), DEFAULT_STALE_S):
        raise ValueError(f"{store} 有新鮮 claim（{rt.queue.claim_owner(store)} 正在跑）——先 stop 那台")
    batch = Batch(rt.depot, store)
    collected = set(inf["collected"])
    results = batch.results(known_ids=collected)
    patterns = batch.patterns() if results else {}
    for rid in sorted(results):
        rec = _to_record(rt, inf, rid, results[rid], patterns)
        collected.add(rid)
        if rec is not None and rt.db.add(rec):
            rt.event("record_added", id=rid, store=store, score=rec.score, kind=rec.kind)
    rt.depot.delete(key)
    n_done, n_error = _counts(batch)
    rt.queue.mark_done(store, f"abandon:{by}", n_done=n_done, n_error=n_error,
                       error_ids=sorted(i for i, r in batch.results().items() if r.get("status") != STATUS_DONE))
    missing = len(set(inf["ids"]) - collected)
    rt.event("batch_abandoned", store=store, by=by, n_collected=len(collected), n_missing=missing)
    return {"store": store, "n_collected": len(collected), "n_missing": missing}
