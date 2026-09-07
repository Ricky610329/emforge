"""算法 client：送件與完成分離；同機與遠端使用同一介面。"""
import time
import uuid

import numpy as np

from . import profiles, submissions
from .db import Database
from .depot import open_depot
from .model import Record


class Client:
    def __init__(self, depot, profile, name, *, run_id=None, spec=None):
        self.depot = open_depot(depot)
        self.profile, self.name = profile, name
        self.run_id = run_id or "run_" + uuid.uuid4().hex
        self.spec = spec

    def submit(self, patterns, *, parents=None, tags=None, preds=None, notes=None, request_id=None):
        pats = list(patterns)
        n = len(pats)
        for values in (parents, tags, preds, notes):
            if values is not None and len(values) != n:
                raise ValueError("每筆候選的附註長度必須一致")
        items = []
        for i, pattern in enumerate(pats):
            note = dict(notes[i]) if notes is not None else {}
            if preds is not None:
                note["pred"] = preds[i]
            items.append(dict(pattern=np.asarray(pattern), parent=parents[i] if parents else None,
                              tag=tags[i] if tags else None, note=note, run_id=self.run_id))
        return submissions.submit(self.depot, self.profile, self.name, self.run_id,
                                  items, request_id, self.spec)

    def _doc(self, sid):
        from . import paths
        doc = self.depot.require_json(paths.submission(self.profile, sid))
        if (doc["name"], doc["run_id"]) != (self.name, self.run_id):
            raise ValueError("送件不屬於此算法執行")
        return doc

    def status(self, sid):
        return submissions.resolve(self.depot, self._doc(sid))

    def wait(self, sid, *, timeout_s, poll_s=1, until="completed"):
        if timeout_s < 0 or poll_s <= 0:
            raise ValueError("timeout_s 不得負值、poll_s 必須大於零")
        if until not in ("completed", "dispatched"):
            raise ValueError("until 必須為 completed 或 dispatched")
        deadline = time.monotonic() + timeout_s
        while True:
            st = self.status(sid)
            if st["state"] in (until, "completed", "rejected"):
                return st
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"等待 {sid} 的 {until} 逾時；可持原 sid 再查")
            time.sleep(min(poll_s, left))

    def results(self, sid):
        return [Record.from_meta(r["meta"], r["bits"], r["response"])
                for r in submissions.results(self.depot, self._doc(sid))]

    @property
    def db(self):
        return Database(self.depot).view(self.profile, strategy=self.name)

    def log(self, event, **fields):
        from . import paths
        from .model import now_iso
        self.depot.append(paths.algo_log(self.profile, self.run_id),
                          {**fields, "event": event, "at": now_iso()})

    @property
    def description(self):
        p = profiles.get_profile(self.profile)
        return {"shape": p.shape, "fixed_on": p.fixed_on, "labels": p.labels}
