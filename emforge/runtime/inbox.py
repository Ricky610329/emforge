"""候選先共用、再按優先級與 run 輪替派工；每個 job 只認領一筆。"""
import dataclasses

from .. import paths, priority, submissions
from ..model import record_id
from .dispatch import dispatch


def _reference(rt, doc, index, rid):
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


def _prepare(rt, doc, props, default):
    """先解析所有引用，即使 max_inflight 已滿仍提升共享候選的優先級。"""
    from ..costs import usage
    run = rt.depot.get_json(paths.algorithm_run(doc["run_id"]))
    remaining = run["identity"]["budget"] - usage(rt.depot, rt.profile_name, doc["run_id"])["new_measurements"] if run else None
    old = {x["index"]: x for x in submissions.resolve(rt.depot, doc).get("items", [])}
    refs, candidates = [], []
    for i, prop in enumerate(props):
        rid = record_id(prop.pattern, rt.profile_name)
        ref = dict(old.get(i, {}))
        if not ref.get("store") and ref.get("state") not in ("error", "rejected"):
            ref = _reference(rt, doc, i, rid) or {"index": i, "id": rid, "state": "received"}
        prio = priority.value(prop.priority, default)
        ref.update(priority=prop.priority, purpose=prop.purpose, effective_prio=prio)
        if ref.get("store") and ref["state"] not in ("done", "error"):
            rt.queue.raise_priority(ref["store"], prio)
        elif not ref.get("store") and ref["state"] == "received":
            if remaining is not None and remaining <= 0:
                ref.update(state="rejected", reason="執行的新量測候選預算已用完")
            else:
                candidates.append((prio, i, prop))
        refs.append(ref)
    return refs, sorted(candidates, key=lambda x: (x[0], x[1]))


def plans(rt, config):
    """送件完整驗證後才共用與提升，不讓壞送件改變排程。"""
    out = []
    turns = rt.state.setdefault("inbox_turns", {})
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
        refs, candidates = _prepare(rt, doc, props, config.prio)
        if not candidates or any(r.get("store") or r["state"] == "rejected" for r in refs):
            rt.depot.put_json(key, {"sid": doc["sid"], "state": "received", "items": refs})
        if candidates:
            prio, index, prop = candidates[0]
            out.append({"doc": doc, "refs": refs, "prio": prio, "index": index, "prop": prop})
    return sorted(out, key=lambda x: (x["prio"], turns.get(x["doc"]["run_id"], 0),
                                      x["doc"].get("order", 0), x["doc"]["sid"]))


def take(rt, config, budget=1, plan=None):
    candidates = plans(rt, config) if plan is None else [plan]
    if not candidates or budget <= 0:
        return None
    plan = candidates[0]
    doc, prop = plan["doc"], plan["prop"]
    prop = dataclasses.replace(prop, note={**prop.note, "_submission": doc["sid"]})
    store = dispatch(rt, config.name, [prop], tick=rt.state["tick"], seed=0,
                     prio=plan["prio"], origin="inbox")
    for i, ref in enumerate(plan["refs"]):
        if ref["state"] == "received":
            found = _reference(rt, doc, ref["index"], ref["id"])
            if found:
                plan["refs"][i] = {**ref, **found}
    rt.depot.put_json(paths.submission_status(rt.profile_name, doc["sid"]),
                      {"sid": doc["sid"], "state": "received", "items": plan["refs"]})
    if store:
        rt.state.setdefault("inbox_turns", {})[doc["run_id"]] = rt.state["tick"]
        rt.strategy_state(config.name)["last_dispatch_tick"] = rt.state["tick"]
        rt.event("inbox_received", name=config.name, sid=doc["sid"], n=1)
    return store
