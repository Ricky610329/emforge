"""emforge/runtime/notarize.py — 公證：破榜候選 → 自動重測 ×repeat_n → 一致性 → 寫 pending。

鐵則：**迴圈不自己加冕**。這裡只寫 `pending.jsonl`，永不碰 ledger；換王是人／AI 的 `promote`。
這是整個 runtime 唯一把 `kind=repeat` 交給 dispatch 的地方（D7）。
門檻＝max(榜首分數, 待審中最好的保守值, 公證中的候選)：沒破就不重測（冷啟動會頻繁公證——數值是佔位，待實測）。
"""
from datetime import datetime

from .. import fs, paths
from ..ledger import Ledger, LedgerTamper
from ..model import KIND_REPEAT, KIND_SAMPLE, STATUS_DONE, Proposal
from .dispatch import dispatch


def notarize_step(rt, new_records: list) -> None:
    cfg = rt.config.runtime
    nz = rt.state.setdefault("notarize", {})
    _complete_ongoing(rt, nz, cfg)
    _open_candidates(rt, nz, cfg, new_records)


def _complete_ongoing(rt, nz: dict, cfg) -> None:
    """重測批全部到終態（inflight 都收尾了）→ 用手上的分數判一致性；有 store 死了就以其餘的判，不等到天荒地老。"""
    #? fail 的重測 store 不擋判定（review-2 後 fail 不是終態、inflight 會留著）：以其餘的判；若之後被接管完成，紀錄照常入庫。
    still_running = {i["store"] for i in rt.inflight() if rt.queue.state(i["store"]) != "fail"}
    for rid, info in list(nz.items()):
        if any(s in still_running for s in info["stores"]):
            continue
        repeats = [r for r in rt.db.measurements(rt.profile_name, rid)
                   if r.kind == KIND_REPEAT and r.run["store"] in info["stores"] and r.score is not None]
        scores = [info["score"]] + [r.score for r in repeats]
        del nz[rid]
        if len(scores) < 2:
            rt.event("notarize_reject", id=rid, scores=scores, spread=None, noise_floor=cfg.noise_floor)
            continue
        spread, conservative = max(scores) - min(scores), min(scores)
        if spread <= cfg.noise_floor:
            fs.append_jsonl(paths.pending_jsonl(rt.root, rt.profile_name),
                            {"id": rid, "tick": rt.state["tick"], "scores": scores, "conservative": conservative,
                             "spread": spread, "stores": info["stores"], "at": fs.now_iso(), "status": "待審（迴圈不加冕）"})
            rt.event("notarize_pass", id=rid, scores=scores, conservative=conservative, spread=spread)
        else:
            rt.event("notarize_reject", id=rid, scores=scores, spread=spread, noise_floor=cfg.noise_floor)


def _open_candidates(rt, nz: dict, cfg, new_records: list) -> None:
    threshold = _threshold(rt, nz)
    pending_ids = {e["id"] for e in fs.read_jsonl(paths.pending_jsonl(rt.root, rt.profile_name))}
    cands = [r for r in new_records if r.status == STATUS_DONE and r.score is not None and r.kind == KIND_SAMPLE]
    for rec in sorted(cands, key=lambda r: -r.score):
        if rec.id in nz or rec.id in pending_ids:
            continue
        if threshold is not None and rec.score <= threshold:
            continue
        stores = []
        try:
            for n in range(1, cfg.repeat_n + 1):
                store = paths.notarize_store_name(rt.profile_name, rt.state["tick"], rec.id, n)
                stores.append(dispatch(rt, "notarize", [Proposal(pattern=rec.bits, parent=rec.id, arm=rec.arm)],
                                       tick=rt.state["tick"], seed=0, prio=cfg.notarize_prio, kind=KIND_REPEAT, store=store))
        except Exception as e:  # noqa: BLE001 — 派不出去就不登記候選（review-3）；派出去的那半批照常收、只是不參與判定
            rt.event("dispatch_failed", name="notarize", tick=rt.state["tick"], error=f"{type(e).__name__}: {e}")
            continue
        nz[rec.id] = {"stores": stores, "tick": rt.state["tick"], "score": rec.score}
        rt.event("record_candidate", id=rec.id, score=rec.score, prev_best=threshold)
        rt.event("notarize_dispatched", id=rec.id, stores=stores)
        threshold = rec.score


def smoke_dispatch(rt, rec_id: str, *, n: int = 1, machine: str | None = None, by: str = "cli") -> list:
    """`emforge smoke`：對一個已量 id 派 n 個重測批（kind=repeat、strategy=cli:smoke、可釘機）。
    切換新機／換版本後驗「同一儀器」用（同機噪音地板 ≈ 0）。結果照常被 runtime collect 入庫，不進候選、不比較。"""
    ms = rt.db.measurements(rt.profile_name, rec_id)
    if not ms:
        raise ValueError(f"{rec_id} 不在 db/{rt.profile_name}/")
    rec = ms[0]
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    stores = []
    for k in range(1, n + 1):
        store = paths.smoke_store_name(rt.profile_name, rec_id, k, stamp)
        stores.append(dispatch(rt, "cli:smoke", [Proposal(pattern=rec.bits, parent=rec_id, arm=rec.arm)],
                               tick=rt.state["tick"], seed=0, prio=rt.config.runtime.notarize_prio, kind=KIND_REPEAT,
                               store=store, origin="cli:smoke", machine=machine))
    return stores


def _threshold(rt, nz: dict):
    vals = []
    lg = Ledger(rt.root, rt.profile_name, rt.profile.spec)
    if lg.exists():
        try:
            best = lg.best()                        # 走 checksum（review：以前直接讀檔、繞過 tamper 檢查）
            if best and best.get("score") is not None:
                vals.append(best["score"])
        except LedgerTamper as e:
            rt.event("ledger_tamper", spec=rt.profile.spec, detail=str(e))
    vals += [e["conservative"] for e in fs.read_jsonl(paths.pending_jsonl(rt.root, rt.profile_name))
             if e.get("conservative") is not None]
    vals += [info["score"] for info in nz.values()]
    return max(vals) if vals else None
