"""平台語意操作；HTTP、MCP 與本機 client 共用，寫量測仍只有 runtime。"""
from .. import paths, profiles, submissions, specs
from ..db import Database
from ..depot import open_depot
from ..model import now_iso
from .evaluate import EvaluationOperations, EVALUATION_OPERATIONS
from .runs import RunOperations, RUN_OPERATIONS

OPERATIONS = {"description", "submit", "submission_status", "submission_results", "algorithm_log",
              "db_query", "db_top", "db_sample", "db_lineage", "db_children", "db_runs", "db_profiles",
              "inbox_list"}


def record_wire(rec):
    return {"meta": rec.meta(), "bits": rec.bits.astype(int).tolist(),
            "response": rec.response.tolist() if rec.response is not None else None}


class Platform(RunOperations, EvaluationOperations):
    def __init__(self, depot):
        self.depot = open_depot(depot)

    def call(self, op, **params):
        if op not in OPERATIONS | RUN_OPERATIONS | EVALUATION_OPERATIONS:
            raise ValueError(f"未知平台操作：{op}")
        return getattr(self, op)(**params)

    def description(self, profile):
        p = profiles.get_profile(profile)
        return {"name": p.name, "shape": list(p.shape), "fixed_on": p.fixed_on.astype(int).tolist(),
                "labels": list(p.labels), "spec": p.spec, "profile_hash": p.profile_hash,
                "measure": p.measure, "spec_snapshot": specs.snapshot(p.spec)}

    def submit(self, profile, name, run_id, items, request_id=None, spec=None):
        spec_snapshot = None
        run = self.depot.get_json(paths.algorithm_run(run_id))
        if run:
            identity = run["identity"]
            if (identity["name"], identity["profile"]) != (name, profile):
                raise ValueError("run_id 的算法或 profile 不符")
            if spec is not None and spec != identity["spec"]:
                raise ValueError("執行固定評估版本，不能中途換 spec")
            spec = identity["spec"]
            spec_snapshot = identity.get("spec_snapshot")
            if run["desired"] != "running":
                raise ValueError("執行已停止接收新送件")
        return submissions.submit(self.depot, profile, name, run_id, items, request_id, spec, spec_snapshot)

    def _doc(self, profile, name, run_id, sid):
        doc = self.depot.require_json(paths.submission(profile, sid))
        if (doc["name"], doc["run_id"]) != (name, run_id):
            raise ValueError("送件不屬於此算法執行")
        return doc

    def submission_status(self, **identity):
        return submissions.resolve(self.depot, self._doc(**identity))

    def submission_results(self, **identity):
        return submissions.results(self.depot, self._doc(**identity))

    def algorithm_log(self, profile, run_id, event, fields):
        self.depot.append(paths.algo_log(profile, run_id), {**fields, "event": event, "at": now_iso()})

    def _view(self, profile):
        profiles.get_profile(profile)
        return Database(self.depot).view(profile)

    def db_query(self, profile, **filters):
        return [record_wire(r) for r in self._view(profile).query(**filters)]

    def db_top(self, profile, k):
        if not isinstance(k, int) or k < 0:
            raise ValueError("k 必須非負整數")
        return [record_wire(r) for r in self._view(profile).top(k)] if k else []

    def db_sample(self, profile, n, seed, **filters):
        return [record_wire(r) for r in self._view(profile).sample(n, seed=seed, **filters)]

    def db_lineage(self, profile, rec_id, depth=10):
        return [record_wire(r) for r in self._view(profile).lineage(rec_id, depth)]

    def db_children(self, profile, rec_id):
        return [record_wire(r) for r in self._view(profile).children(rec_id)]

    def db_runs(self, profile, strategy=None):
        return self._view(profile).runs(strategy)

    def db_profiles(self):
        return [self.description(p.name) for p in profiles.all_profiles()]

    def inbox_list(self, profile):
        return [{"name": d["name"], "run_id": d["run_id"],
                 **submissions.resolve(self.depot, d)} for d in submissions.documents(self.depot, profile)]
