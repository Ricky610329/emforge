"""指定機器的算法執行命令。每次更新持 run 鎖，停止要求不被心跳覆寫。"""
import uuid

import yaml

from .. import algorithms, paths, profiles, specs
from ..model import canonical_json, now_iso

RUN_OPERATIONS = {"algorithm_register", "algorithm_package", "run_start", "run_stop", "run_status",
                  "run_list", "run_logs", "node_heartbeat", "node_runs", "run_claim", "run_report"}
TERMINAL = {"completed", "failed", "stopped", "interrupted"}


class RunOperations:
    def algorithm_register(self, name, files, entrypoint="main.py", requires=(), checkpoint_schema=None):
        if not paths.is_valid_name(name) or name in paths.RESERVED_STRATEGY_NAMES:
            raise ValueError("算法名稱不合法")
        version, doc = algorithms.package(files, entrypoint, requires, checkpoint_schema)
        self.depot.put_json(paths.algorithm_package(version), doc)
        self.depot.put_json(paths.algorithm_version(name, version), {"name": name, "version": version})
        return version

    def algorithm_package(self, version):
        return self.depot.require_json(paths.algorithm_package(version))

    def node_heartbeat(self, node, session, environments, max_runs=1):
        key = paths.algorithm_node(node)
        with self.depot.lock(paths.platform_lock("node_" + node), owner=uuid.uuid4().hex):
            old = self.depot.get_json(key)
            if old and old["session"] != session:
                if self.depot.now() - old["heartbeat"] < 90:
                    raise ValueError("此算法節點已有活躍執行端")
                if any(r["state"] not in TERMINAL | {"queued"} for r in self.run_list(node=node)):
                    raise ValueError("舊節點仍有未確認結束的算法，不能接管")
            self.depot.put_json(key, {"node": node, "session": session, "environments": environments,
                                     "max_runs": max_runs, "heartbeat": self.depot.now()})
        return {"node": node}

    def _ensure_inbox(self, profile, name):
        key = paths.strategies_yaml(profile)
        with self.depot.lock(paths.platform_lock("config_" + profile), owner=uuid.uuid4().hex):
            cfg = yaml.safe_load(self.depot.require_bytes(key))
            entries = cfg.setdefault("strategies", [])
            found = next((x for x in entries if x["name"] == name), None)
            if found and found.get("kind", "propose") != "inbox":
                raise ValueError("算法名稱與 propose 策略衝突")
            if not found:
                entries.append({"name": name, "kind": "inbox", "prio": 3, "batch": 32, "max_inflight": 1})
                self.depot.put_bytes(key, yaml.safe_dump(cfg).encode("utf-8"))

    def run_start(self, name, version, run_id, node, environment, profile, params=None, seed=0,
                  budget=100, spec=None, resume_from=None):
        package = self.algorithm_package(version)
        self.depot.require_json(paths.algorithm_version(name, version))
        p = profiles.get_profile(profile)
        spec = spec or p.spec
        if specs.get_spec(spec).measure != p.measure:
            raise ValueError("spec 與 profile 不相容")
        if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
            raise ValueError("budget 必須為正整數")
        identity = dict(name=name, version=version, run_id=run_id, node=node, environment=environment,
                        profile=profile, params=params or {}, seed=seed, budget=budget, spec=spec, resume_from=resume_from)
        with self.depot.lock(paths.platform_lock(run_id), owner=uuid.uuid4().hex):
            old = self.depot.get_json(paths.algorithm_run(run_id))
            if old:
                if canonical_json(old["identity"]) != canonical_json(identity):
                    raise ValueError("相同 run_id 不可換版本或參數；請建立新執行")
                return self.run_status(run_id)
            host = self.depot.get_json(paths.algorithm_node(node))
            if not host or self.depot.now() - host["heartbeat"] >= 90:
                raise ValueError("指定算法節點不存在或離線")
            if environment not in host["environments"]:
                raise ValueError("指定 Python 環境未登記")
            if resume_from:
                previous = self.run_status(resume_from)
                previous_pkg = self.algorithm_package(previous["identity"]["version"])
                if (previous["state"] not in TERMINAL or previous["identity"]["node"] != node
                        or not package["checkpoint_schema"]
                        or previous_pkg["checkpoint_schema"] != package["checkpoint_schema"]):
                    raise ValueError("續跑需同節點已結束執行且明確宣告相容 checkpoint_schema")
            self._ensure_inbox(profile, name)
            self.depot.put_json(paths.algorithm_run(run_id), {"identity": identity, "state": "queued",
                                "desired": "running", "at": now_iso(), "session": None})
        return self.run_status(run_id)

    def run_status(self, run_id):
        doc = self.depot.require_json(paths.algorithm_run(run_id))
        node = self.depot.get_json(paths.algorithm_node(doc["identity"]["node"]))
        return {**doc, "run_id": run_id, "offline": not node or self.depot.now() - node["heartbeat"] >= 90}

    def run_list(self, node=None):
        docs = [self.depot.get_json(k) for k in self.depot.list(paths.algorithm_runs_dir()) if k.endswith(".json")]
        return [self.run_status(d["identity"]["run_id"]) for d in docs if d and (node is None or d["identity"]["node"] == node)]

    def run_stop(self, run_id):
        with self.depot.lock(paths.platform_lock(run_id), owner=uuid.uuid4().hex):
            doc = self.depot.require_json(paths.algorithm_run(run_id))
            doc["desired"] = "stopped"
            if doc["state"] == "queued":
                doc["state"] = "stopped"
            self.depot.put_json(paths.algorithm_run(run_id), doc)
        return self.run_status(run_id)

    def node_runs(self, node, session):
        self._check_node(node, session)
        return self.run_list(node=node)

    def _check_node(self, node, session):
        host = self.depot.require_json(paths.algorithm_node(node))
        if host["session"] != session:
            raise ValueError("算法節點 session 已失效")

    def run_claim(self, run_id, node, session):
        self._check_node(node, session)
        with self.depot.lock(paths.platform_lock(run_id), owner=uuid.uuid4().hex):
            doc = self.depot.require_json(paths.algorithm_run(run_id))
            if doc["identity"]["node"] != node or doc["state"] != "queued" or doc["desired"] != "running":
                return False
            doc.update(state="starting", session=session)
            self.depot.put_json(paths.algorithm_run(run_id), doc)
        return True

    def run_report(self, run_id, node, session, state, **fields):
        self._check_node(node, session)
        if state not in TERMINAL | {"running", "stopping"}:
            raise ValueError("不合法算法狀態")
        with self.depot.lock(paths.platform_lock(run_id), owner=uuid.uuid4().hex):
            doc = self.depot.require_json(paths.algorithm_run(run_id))
            if doc["identity"]["node"] != node or doc["session"] != session:
                raise ValueError("此執行不屬於節點 session")
            if doc["state"] in TERMINAL:
                return doc
            doc.update(state=state, detail=fields, updated_at=now_iso())
            for key in ("exit_code", "reason", "pid", "stdout"):
                if key in fields:
                    doc[key] = fields[key]
            self.depot.put_json(paths.algorithm_run(run_id), doc)
        return doc

    def run_logs(self, run_id):
        doc = self.run_status(run_id)
        return {"algorithm": self.depot.read_log(paths.algo_log(doc["identity"]["profile"], run_id)),
                "stdout": doc.get("stdout", "")}
