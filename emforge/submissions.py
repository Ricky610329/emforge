"""不可變送件與量測引用；提出者不因去重失去結果。"""
import uuid
import time

import numpy as np

from dataclasses import asdict
from . import paths, profiles, specs
from .db import Database
from .depot import open_depot
from .model import canonical_json, now_iso, record_id, sha1_hex
from .strategy import validate_proposals


def submit(depot, profile, name, run_id, items, request_id=None, spec=None, spec_snapshot=None):
    depot = open_depot(depot)
    p = profiles.get_profile(profile)
    for value in (name, run_id):
        if not isinstance(value, str) or not paths.is_valid_name(value):
            raise ValueError("算法名稱與 run_id 必須符合命名規則")
    if name in paths.RESERVED_STRATEGY_NAMES:
        raise ValueError("算法名稱為保留字")
    spec = spec or p.spec
    evaluator = specs.frozen(spec, spec_snapshot)
    if evaluator.measure != p.measure:
        raise ValueError("評估器與量測 profile 不相容")
    props = validate_proposals(items, p, len(items))
    if not props:
        raise ValueError("送件不得為空")
    if request_id is not None and (not isinstance(request_id, str) or not paths.is_valid_name(request_id)):
        raise ValueError("request_id 名稱不合法")
    sid = ("s_" + sha1_hex(canonical_json([run_id, request_id]).encode("utf-8"))
           if request_id else "s_" + uuid.uuid4().hex)
    key = paths.submission(profile, sid)
    data = {"profile": profile, "profile_hash": p.profile_hash, "name": name,
            "run_id": run_id, "sid": sid, "spec": spec, "spec_snapshot": asdict(evaluator),
            "items": [encode(p, profile) for p in props]}
    with depot.lock(paths.submission_lock(profile, sid), owner=uuid.uuid4().hex):
        old = depot.get_json(key)
        if old is not None:
            compared = data if "spec_snapshot" in old else {k: v for k, v in data.items() if k != "spec_snapshot"}
            if canonical_json({k: v for k, v in old.items() if k not in ("at", "order")}) != canonical_json(compared):
                raise ValueError("同 request_id 已存在不同送件內容")
            return sid
        depot.put_json(key, {**data, "at": now_iso(), "order": time.time_ns()})
    return sid


def encode(p, profile):
    return {"pattern": np.asarray(p.pattern, bool).astype(int).reshape(-1).tolist(),
            "id": record_id(p.pattern, profile), "parent": p.parent, "tag": p.tag,
            "arm": p.arm, "note": dict(p.note),
            **({"priority": p.priority} if p.priority is not None else {}),
            **({"purpose": p.purpose} if p.purpose is not None else {})}


def proposals(doc, profile):
    raw = [dict(pattern=np.asarray(x["pattern"]).reshape(profile.shape), parent=x.get("parent"),
                tag=x.get("tag"), arm=x.get("arm"), note=x.get("note", {}), run_id=doc["run_id"],
                priority=x.get("priority"), purpose=x.get("purpose"))
           for x in doc["items"]]
    props = validate_proposals(raw, profile, len(raw))
    if any(record_id(p.pattern, profile.name) != x["id"] for p, x in zip(props, doc["items"])):
        raise ValueError("送件內容與 id 不一致")
    return props


def documents(depot, profile, name=None):
    out = []
    for key in depot.list(paths.submissions_dir(profile)):
        if key.endswith(".json"):
            doc = depot.get_json(key)
            if doc and (name is None or doc["name"] == name):
                out.append(doc)
    return sorted(out, key=lambda d: (d.get("order", 0), d["sid"]))


def resolve(depot, doc):
    """依持久引用查進度；每次建立新 Reader，長駐 client 也看得到新結果。"""
    saved = depot.get_json(paths.submission_status(doc["profile"], doc["sid"])) or {}
    if saved.get("state") == "rejected":
        return saved
    db = Database(depot)
    refs = {int(i["index"]): i for i in saved.get("items", [])}
    out, jobs = [], None
    for index, item in enumerate(doc["items"]):
        ref = dict(refs.get(index, {"index": index, "id": item["id"], "state": "received"}))
        if ref.get("store"):
            stem = paths.record_stem(item["id"], ref["store"])
            rec = db.try_load(doc["profile"], stem)
            if rec:
                ref["state"] = "done" if rec.status == "done" else "error"
                ref["reason"] = rec.note.get("error")
            elif ref["state"] not in ("error", "rejected"):
                from .queue import Queue
                collecting = depot.exists(paths.inflight_file(doc["profile"], ref["store"]))
                record_exists = depot.exists(paths.record_by_stem(doc["profile"], stem))
                if not collecting and not record_exists and Queue(depot).state(ref["store"]) == "done":
                    ref.update(state="error", reason="量測批已結束但沒有可用紀錄")
        ref.update(priority=item.get("priority"), purpose=item.get("purpose"))
        if ref.get("store"):
            from .queue import Queue
            if jobs is None:
                jobs = {j.store: j for j in Queue(depot).list()}
            job = jobs.get(ref["store"])
            if job:
                ref["effective_prio"] = job.prio
        out.append(ref)
    states = {r["state"] for r in out}
    if states <= {"done", "error", "rejected"}:
        state = "completed"
    elif "received" not in states:
        state = "dispatched"
    elif states != {"received"}:
        state = "dispatched_partial"
    else:
        state = "received"
    return {"sid": doc["sid"], "state": state, "items": out, "spec": doc["spec"]}


def results(depot, doc):
    db = Database(depot)
    out = []
    for ref in resolve(depot, doc).get("items", []):
        if not ref.get("store"):
            continue
        rec = db.try_load(doc["profile"], paths.record_stem(ref["id"], ref["store"]))
        if rec:
            meta = rec.meta()
            meta["score"] = specs.frozen(doc["spec"], doc.get("spec_snapshot")).score(rec.measure) if rec.status == "done" else None
            out.append({"index": ref["index"], "meta": meta, "bits": rec.bits.astype(int).tolist(),
                        "response": rec.response.tolist() if rec.response is not None else None})
    return out
