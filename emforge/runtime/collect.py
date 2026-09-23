"""emforge/runtime/collect.py — 收結果：增量讀 results/<id>.json → profile_hash 比對 → 量測（凍結）→ 評分（可換）→ 入庫 → 收尾。

#! 回歸 I-10：結果自帶量它的儀器指紋；不符＝有人改了 registry／舊程式在跑 → profile_tamper、不入庫（但記為已收，不重讀）。
#! 回歸 review-1：error 結果在批未 done 前**不**入庫、**不**記 collected——worker 的補測輪可能翻案（同一個結果檔被覆寫成 done），
#  記了就永遠不再讀、翻案的 HFSS 結果白燒。批到 done 終態才把殘留 error 收進 db。
#! 回歸 review-2：`.fail` 不是終態——名單外的機器會接管重跑。fail 只發一次 batch_failed、inflight 留著繼續收；
#  真的沒人接由人 `emforge abandon`（本檔 `abandon`）宣告放棄。
#! 回歸 review-8：每筆入庫後**立刻**落地 collected；上次死在「add 成功、collected 沒落地」之間的那筆，重讀時 add 回 False
#  也算新收（交給 notarize），不能靜默漏掉破榜設計。
#! 回歸 I-21（2026-09-23）：新收的 done 樣本在標 collected **之前**先寫進 state.notarize_deferred 並落地——collected 落地了、
#  notarize 還沒跑就死掉（或 tick 例外），下個 tick 這筆不再是「新收」，破榜候選會永遠不進公證。
#! 回歸 I-19（2026-09-23）：一個 store 收結果炸了（雜結果檔 KeyError、patterns 讀不到）只記 collect_error、其他 store 照收；
#  以前整個 collect 穿出、連錯十個 tick runtime 退出。
#! 回歸 I-18（2026-09-23）：錯誤率看**最近 ERROR_WINDOW 筆樣本**（跨批、至少 MIN_ERROR_SAMPLES 筆）——inbox／背景派的是 n=1 的批，
#  逐批判會讓一筆 error 就暫停整個 profile。
量測與評分只在這裡發生（策略／worker 都不算分，D1）。
"""
import numpy as np

from .. import paths, specs
from ..batches import Batch
from ..model import KIND_SAMPLE, STATUS_DONE, STATUS_ERROR, Record, now_iso

ERROR_WINDOW = 20        #? 錯誤率的滑動視窗（最近幾筆樣本結果，跨批）
MIN_ERROR_SAMPLES = 3    #? 視窗至少幾筆才判（worker 側另有連 5 敗熔斷；這裡是 profile 層的第二道）


def collect(rt) -> list:
    """走一遍所有 inflight；回本次新收到的 Record（含已在庫但沒記到 collected 的）。一個 store 炸了不影響其他 store（I-19）。"""
    new = []
    for inf in rt.inflight():
        try:
            new.extend(_collect_one(rt, inf))
        except Exception as e:  # noqa: BLE001
            rt.event("collect_error", store=inf["store"], error=f"{type(e).__name__}: {e}")
    return new


def _collect_one(rt, inf: dict) -> list:
    store = inf["store"]
    batch = Batch(rt.depot, store)
    qstate = rt.queue.state(store)
    terminal = qstate == "done"
    collected = set(inf["collected"])
    results = batch.results(known_ids=collected)
    new, patterns = [], None
    known_ids = set(inf["ids"])
    for rid in sorted(results):
        res = results[rid]
        if rid not in known_ids:
            rt.event("stray_result", store=store, id=rid)       # 不在 manifest：點名、記為已讀、不入庫（I-19）
            collected.add(rid)
            inf["collected"] = sorted(collected)
            _persist_inflight(rt, inf)
            continue
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
            _defer_for_notarize(rt, rec)        # 先落地候選、再落地 collected（I-21）
        _persist_inflight(rt, inf)
    if qstate == "fail" and not inf.get("fail_reported"):
        inf["fail_reported"] = True
        _persist_inflight(rt, inf)
        rt.event("batch_failed", store=store,
                 reason=f"worker 判死（名單外機器可接管，inflight 保留；沒人接就 abandon）；已收 {len(collected)}/{len(inf['ids'])} 筆")
    if terminal:
        _finalize(rt, inf, collected)
    return new


def _defer_for_notarize(rt, rec: Record) -> None:
    if rec.status != STATUS_DONE or rec.kind != KIND_SAMPLE or rt.readonly:
        return
    deferred = rt.state.setdefault("notarize_deferred", [])
    entry = [rec.id, rec.run["store"]]
    if entry not in deferred:
        deferred.append(entry)
        rt.save_state()


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
            specs.check_passivity(measure)                    # I-29：物理上不可能的響應不是有效設計
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
                  extra=dict(res.get("extra") or {}), tag=item.get("tag"), run_id=item.get("run_id"))


def _counts(batch: Batch) -> tuple:
    results = batch.results()
    n_done = sum(1 for r in results.values() if r.get("status") == STATUS_DONE)
    return n_done, len(results) - n_done


def _received_counts(rt, inf):
    """以持久化 record 計數，涵蓋前次增量收件與後處理／profile 拒絕。"""
    n_done = 0
    for rid in inf["ids"]:
        stem = paths.record_stem(rid, inf["store"])
        if rt.depot.exists(paths.record_by_stem(rt.profile_name, stem)):
            rec = rt.db.load(rt.profile_name, stem)
            n_done += rec.status == STATUS_DONE
    return n_done, len(inf["ids"]) - n_done


def _finalize(rt, inf: dict, collected: set) -> None:
    """批 done 且能收的都收了 → 移除 inflight、發事件、算錯誤率。"""
    store = inf["store"]
    if not set(inf["ids"]) <= collected:
        return                                  # worker 標 done 但檔還沒讀齊（下個 tick 再收）
    n_done, n_error = _received_counts(rt, inf)
    rt.depot.delete(paths.inflight_file(rt.profile_name, store))
    rt.event("batch_done", store=store, n_done=n_done, n_error=n_error)
    if inf["kind"] == KIND_SAMPLE:
        _update_error_window(rt, store, n_done, n_error)


def _update_error_window(rt, store: str, n_done: int, n_error: int) -> None:
    """最近 ERROR_WINDOW 筆樣本結果（跨批）的錯誤率 > max_error_rate 且至少 MIN_ERROR_SAMPLES 筆 → 暫停 profile（I-18）。"""
    window = rt.state.setdefault("error_window", [])
    window.extend([0] * n_done + [1] * n_error)
    del window[:-ERROR_WINDOW]
    if len(window) < MIN_ERROR_SAMPLES or rt.state.get("paused_profile"):
        return
    rate = sum(window) / len(window)
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
    #! 檢查 #2（2026-09-07）：接管後原主的 mark_fail 讓 .fail 與新鮮 claim 並存，`state()` 回 fail——只看 state()=="claimed"
    #  會放行、把正在量的批標 done、之後的結果沒人收。與 requeue 同一把尺：is_live（有主 claim 且新鮮或批有進度）。
    if rt.queue.is_live(store):
        raise ValueError(f"{store} 有人正在跑（{rt.queue.claim_owner(store)}）——先 stop 那台再 abandon")
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
