"""算法 client：送件與完成分離；同機與遠端使用同一介面。"""
import time
import uuid

import numpy as np

from .client_view import ClientView, records
from .platform.service import Platform
from .platform.transport import RemotePlatform


class Client:
    def __init__(self, depot, profile, name, *, run_id=None, spec=None, token=None):
        self.platform = (RemotePlatform(depot, token=token)
                         if isinstance(depot, str) and depot.startswith(("http://", "https://")) else Platform(depot))
        self.profile, self.name = profile, name
        self.run_id = run_id or "run_" + uuid.uuid4().hex
        self.spec = spec

    def submit(self, patterns, *, parents=None, tags=None, preds=None, notes=None, request_id=None,
               priority=None, priorities=None, purposes=None):
        pats = list(patterns)
        n = len(pats)
        for values in (parents, tags, preds, notes, priorities, purposes):
            if values is not None and len(values) != n:
                raise ValueError("每筆候選的附註長度必須一致")
        items = []
        for i, pattern in enumerate(pats):
            note = dict(notes[i]) if notes is not None else {}
            if preds is not None:
                note["pred"] = preds[i]
            items.append(dict(pattern=np.asarray(pattern).tolist(), parent=parents[i] if parents else None,
                              tag=tags[i] if tags else None, note=note, run_id=self.run_id,
                              priority=priorities[i] if priorities is not None else priority,
                              purpose=purposes[i] if purposes is not None else None))
        return self.platform.call("submit", profile=self.profile, name=self.name, run_id=self.run_id,
                                  items=items, request_id=request_id, spec=self.spec)

    def _identity(self, sid):
        return dict(profile=self.profile, name=self.name, run_id=self.run_id, sid=sid)

    def status(self, sid):
        return self.platform.call("submission_status", **self._identity(sid))

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
        return records(self.platform.call("submission_results", **self._identity(sid)))

    @property
    def db(self):
        return ClientView(self.platform, self.profile, self.name)

    def log(self, event, **fields):
        return self.platform.call("algorithm_log", profile=self.profile, run_id=self.run_id,
                                  event=event, fields=fields)

    @property
    def description(self):
        return self.platform.call("description", profile=self.profile)
