"""HTTP／CLI／MCP 的共同評估服務，從原始 measure 重算同一 spec。"""
from .. import costs, evaluation, paths, profiles, report, specs
from ..db import Database
from ..device.states import read_fleet

EVALUATION_OPERATIONS = {"evaluate", "report", "run_usage", "platform_state"}

class EvaluationOperations:
    def evaluate(self, profile, kind="curve", strategy=None, run_id=None, spec=None, since_tick=None):
        p = profiles.get_profile(profile)
        run = self.depot.get_json(paths.algorithm_run(run_id)) if run_id else None
        if run and run["identity"]["profile"] != profile:
            raise ValueError("run 不屬於此 profile")
        frozen = run["identity"]["spec"] if run else None
        if frozen and spec and spec != frozen:
            raise ValueError("run 評估須使用執行時固定的 spec")
        evaluator = specs.frozen(frozen or spec or p.spec, run["identity"].get("spec_snapshot") if run else None)
        if evaluator.measure != p.measure:
            raise ValueError("spec 與 profile 的 measure 不相容")
        records = Database(self.depot).view(profile).query()
        rows = [r.meta() for r in records if (strategy is None or r.strategy == strategy)
                and (run_id is None or r.run_id == run_id)
                and (since_tick is None or (r.tick is not None and r.tick >= since_tick))]
        rows = evaluation.samples(rows)
        for row in rows:
            row["score"] = evaluator.score(row["measure"]) if row["status"] == "done" else None
        if kind == "curve":
            result = evaluation.efficiency_curve(rows)
        elif kind == "calibration":
            result = evaluation.calibration(rows)
        elif kind == "metrics":
            result = evaluation.metric_summary(rows, evaluator)
        else:
            raise ValueError("kind 必須是 curve、calibration 或 metrics")
        return {"profile": profile, "spec": evaluator.name, "strategy": strategy, "run_id": run_id,
                "since_tick": since_tick, "n": len(rows), "result": result,
                "scope": "新增 sample 的觀察統計；共用與 repeat 不當獨立樣本，不代表因果優勢"}

    def report(self, profiles, cross_profile=False, k_min=None):
        return report.report(self.depot, profiles, cross_profile=cross_profile, k_min=k_min)

    def run_usage(self, profile, run_id):
        return costs.usage(self.depot, profile, run_id)

    def platform_state(self):
        nodes = [self.depot.get_json(k) for k in self.depot.list(paths.algorithm_nodes_dir()) if k.endswith(".json")]
        return {"runs": self.run_list(), "profiles": self.db_profiles(), "fleet": read_fleet(self.depot),
                "nodes": [{**n, "offline": self.depot.now()-n["heartbeat"] >= 90} for n in nodes if n],
                "runtimes": {p.name: self.depot.get_json(paths.status_json(p.name)) for p in profiles.all_profiles()}}
