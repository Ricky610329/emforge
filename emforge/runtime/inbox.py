"""收件匣排程：只由持有 profile 鎖的 runtime 派工。"""
import dataclasses

from .. import paths, submissions
from ..model import record_id
from .dispatch import dispatch


def _reference(rt, doc, index, rid):
    """先找量測，再找進行中工作；dispatch 後 status 未寫也可重建引用。"""
    ms = [r for r in rt.db.measurements(rt.profile_name, rid) if r.kind == "sample" and r.status == "done"]
    if ms:
        rec = min(ms, key=lambda r: r.score if r.score is not None else float("inf"))
        return {"index": index, "id": rid, "store": rec.run["store"], "state": "done",
                "shared": rec.note.get("_submission") != doc["sid"]}
    for inf in rt.inflight():
        if rid in inf["ids"] and inf["kind"] == "sample":
            return {"index": index, "id": rid, "store": inf["store"], "state": "dispatched",
                    "shared": inf["items"][rid].get("note", {}).get("_submission") != doc["sid"]}
    return None


def _prepare(rt, doc, props, budget):
    status = submissions.resolve(rt.depot, doc)
    old = {x["index"]: x for x in status.get("items", [])}
    refs, selected, seen = [], [], set()
    for i, prop in enumerate(props):
        rid = record_id(prop.pattern, rt.profile_name)
        ref = old.get(i, {})
        if ref.get("store") or ref.get("state") in ("error", "rejected"):
            refs.append(ref)
            continue
        found = _reference(rt, doc, i, rid)
        if found:
            refs.append(found)
        elif len(selected) < budget and rid not in seen:
            selected.append(dataclasses.replace(prop, note={**prop.note, "_submission": doc["sid"]}))
            seen.add(rid)
            refs.append({"index": i, "id": rid, "state": "received"})
        else:
            refs.append({"index": i, "id": rid, "state": "received"})
    return selected, refs


def take(rt, config, budget):
    """每策略一個 tick 最多派一批；等待共用結果的舊送件不阻擋下一份。"""
    for doc in submissions.documents(rt.depot, rt.profile_name, config.name):
        key = paths.submission_status(rt.profile_name, doc["sid"])
        status = submissions.resolve(rt.depot, doc)
        if status["state"] in ("completed", "rejected"):
            continue
        try:
            if doc["profile_hash"] != rt.profile.profile_hash:
                raise ValueError("profile_hash 不一致")
            props = submissions.proposals(doc, rt.profile)
        except (ValueError, KeyError, TypeError) as e:
            rt.depot.put_json(key, {"sid": doc["sid"], "state": "rejected", "reason": str(e), "items": []})
            rt.event("inbox_rejected", name=config.name, sid=doc["sid"], reason=str(e))
            continue
        selected, refs = _prepare(rt, doc, props, budget)
        store = None
        if selected:
            store = dispatch(rt, config.name, selected, tick=rt.state["tick"], seed=0, prio=config.prio,
                             origin="inbox")
        for i, ref in enumerate(refs):
            if ref["state"] == "received":
                found = _reference(rt, doc, i, ref["id"])
                if found:
                    refs[i] = found
        rt.depot.put_json(key, {"sid": doc["sid"], "state": "received", "items": refs})
        if store:
            rt.event("inbox_received", name=config.name, sid=doc["sid"], n=len(selected))
            return store
    return None
