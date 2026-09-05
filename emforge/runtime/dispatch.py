"""emforge/runtime/dispatch.py — 派工：去重 → **先寫 inflight** → 寫批 → 加 job → 事件。

#! 回歸 I-13（2026-08-17）：派工沒掛偵測＝鏈斷睡死。inflight 先落地：job 寫失敗會剩一個「inflight 無 job」
#  給 reconcile 抓；反過來（job 在、inflight 不在）才是沒人收結果的死鏈。
去重＝資料庫已量成功的 id ∪ 所有 inflight 的 id ∪ 批內重複；**沒有旁路參數**（D7）。
唯一例外是 kind=repeat（公證重測：同 id 就是要再量），只有 runtime/notarize.py 會這樣呼叫。
"""
import numpy as np

from .. import fs, paths
from ..batches import Batch
from ..model import KIND_REPEAT, KIND_SAMPLE, KINDS, Job, record_id


class StoreExists(Exception):
    """同名 store 已有 inflight 或批——重播了同一個 tick？在寫任何東西之前拒絕（review-3）。"""


def dispatch(rt, strategy_name: str, proposals: list, *, tick: int, seed: int, prio: int,
             kind: str = KIND_SAMPLE, store: str | None = None, origin: str = "runtime",
             machine: str | None = None) -> str | None:
    """回 store 名；全部重複 → None（什麼都不寫）。`origin`／`machine` 只有 CLI smoke 會給（釘機重測）。"""
    if kind not in KINDS:
        raise ValueError(f"kind 必須是 {KINDS}，得到 {kind!r}")
    if (kind == KIND_REPEAT) != (store is not None):
        raise ValueError("sample 批的 store 名由 runtime 決定；repeat 批必須指定 store")
    profile = rt.profile
    store = store or paths.store_name(profile.name, strategy_name, tick)
    if paths.inflight_file(rt.root, profile.name, store).exists() or Batch(rt.root, store).exists():
        raise StoreExists(f"store {store} 已存在（inflight 或批）——tick 號重播？什麼都沒寫")
    keep, dropped = _dedup(rt, proposals, kind)
    rt.event("proposals_validated", name=strategy_name, tick=tick, n_in=len(proposals), n_dup=dropped, n_out=len(keep))
    if not keep:
        return None
    ids = [record_id(p.pattern, profile.name) for p in keep]
    items = {rid: {"parent": p.parent, "arm": p.arm, "note": dict(p.note)} for rid, p in zip(ids, keep)}
    fs.atomic_write_json(paths.inflight_file(rt.root, profile.name, store),
                         {"store": store, "strategy": strategy_name, "tick": tick, "seed": seed, "kind": kind,
                          "prio": prio, "ids": ids, "items": items, "collected": [], "at": fs.now_iso()})
    manifest = {"store": store, "sim_profile": profile.name, "profile_hash": profile.profile_hash,
                "strategy": strategy_name, "tick": tick, "seed": seed, "prio": prio, "kind": kind,
                "items": [{"id": rid, **items[rid]} for rid in ids]}
    Batch(rt.root, store).write(manifest, np.stack([p.pattern for p in keep]), ids)
    rt.queue.add(Job(store=store, sim_profile=profile.name, profile_hash=profile.profile_hash, prio=prio,
                     n=len(ids), machine=machine, origin=origin, by=origin))
    rt.event("batch_dispatched", store=store, strategy=strategy_name, n=len(ids), prio=prio, tick=tick, seed=seed,
             kind=kind)
    if kind == KIND_SAMPLE:
        rt.strategy_state(strategy_name)["n_dispatched"] += 1
    return store


def _dedup(rt, proposals: list, kind: str) -> tuple:
    if kind == KIND_REPEAT:
        return list(proposals), 0          #? 公證重測：同 id 再量是目的，不是重複
    known = rt.db.ids(rt.profile.name) | {rid for inf in rt.inflight() for rid in inf["ids"]}
    seen, keep, dropped = set(), [], 0
    for p in proposals:
        rid = record_id(p.pattern, rt.profile.name)
        if rid in known or rid in seen:
            dropped += 1
            continue
        seen.add(rid)
        keep.append(p)
    return keep, dropped
