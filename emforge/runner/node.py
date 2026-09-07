"""指定機器的算法執行端：拉取命令、隔離行程、保存輸出，不自動搬到其他機器。"""
import json
import os
import time
import uuid
from pathlib import Path

from .. import paths
from ..depot import FileDepot
from ..platform.transport import RemotePlatform
from ..platform.runs import TERMINAL
from .environment import inspect_environment, prepare
from .process import OwnedProcess, process_identity


class Runner:
    def __init__(self, root, endpoint, node, environments, *, token=None, max_runs=1, stop_timeout_s=30):
        self.root = Path(root)
        self.endpoint, self.node, self.environments = endpoint, node, dict(environments)
        if max_runs < 1 or stop_timeout_s < 0:
            raise ValueError("max_runs 必須正數、停止寬限不得負值")
        self.max_runs, self.stop_timeout_s = max_runs, stop_timeout_s
        self.platform = RemotePlatform(endpoint, token=token)
        self.local = FileDepot(root)
        self.owned, self.fresh = {}, True
        self.session = self._lock()
        self._closed = False

    def _lock(self):
        key = paths.runner_lock()
        previous = self.local.owner(key)
        if previous:
            if process_identity(previous["pid"]) == previous.get("birth"):
                raise ValueError("同工作目錄已有算法執行端")
            self.local.release(key, owner=previous["owner"])
        identity = self.local.get_json(paths.runner_identity())
        if identity and identity["node"] != self.node:
            raise ValueError("工作目錄已綁定其他節點")
        session = identity["session"] if identity else uuid.uuid4().hex
        if not self.local.claim(key, {"owner": session, "pid": os.getpid(), "birth": process_identity(os.getpid())}):
            raise ValueError("算法執行端目錄已被認領")
        self.local.put_json(paths.runner_identity(), {"node": self.node, "session": session})
        return session

    def tick(self):
        self.platform.call("node_heartbeat", node=self.node, session=self.session,
                           environments=list(self.environments), max_runs=self.max_runs)
        jobs = self.platform.call("node_runs", node=self.node, session=self.session)
        for doc in jobs:
            rid = doc["run_id"]
            if rid in self.owned:
                self._observe(doc)
            elif doc["state"] not in TERMINAL | {"queued"}:
                self._report(rid, "interrupted", reason="執行端重啟；保留 checkpoint，未自動重開算法",
                             stdout=self._stdout(rid))
            elif doc["state"] == "queued" and len(self.owned) < self.max_runs:
                self._start(doc)
        self.fresh = False

    def _start(self, doc):
        identity, rid = doc["identity"], doc["run_id"]
        if not self.platform.call("run_claim", run_id=rid, node=self.node, session=self.session):
            return
        out = None
        try:
            package = self.platform.call("algorithm_package", version=identity["version"])
            python = self.environments[identity["environment"]]
            report = inspect_environment(python, package["requires"])
            entry = prepare(self.root, identity, identity["version"], package)
            env = self._environment(identity)
            out = paths.runner_stdout(self.root, rid).open("ab")
            process = OwnedProcess(python, entry, paths.runner_work(self.root, rid), env, out)
            self.owned[rid] = {"process": process, "out": out, "stop_at": None}
            self._report(rid, "running", pid=process.pid, environment=report)
        except Exception as e:
            if rid in self.owned:
                self._terminate(rid)
            elif out:
                out.close()
            self._report(rid, "failed", reason=f"{type(e).__name__}: {e}", stdout=self._stdout(rid))

    def _environment(self, identity):
        env = dict(os.environ)
        env.update(PYTHONIOENCODING="utf-8", EMFORGE_ENDPOINT=self.endpoint, EMFORGE_ALGORITHM=identity["name"],
                   EMFORGE_RUN_ID=identity["run_id"], EMFORGE_PROFILE=identity["profile"], EMFORGE_SPEC=identity["spec"],
                   EMFORGE_PARAMS=json.dumps(identity["params"]), EMFORGE_SEED=str(identity["seed"]),
                   EMFORGE_RUN_STOP=str(paths.runner_stop(self.root, identity["run_id"])))
        if self.platform.token:
            env["EMFORGE_PLATFORM_TOKEN"] = self.platform.token
        if identity.get("resume_from"):
            checkpoint = paths.runner_work(self.root, identity["resume_from"])
            if not checkpoint.is_dir():
                raise ValueError("本機沒有來源 checkpoint 目錄")
            env["EMFORGE_RESUME_FROM"] = str(checkpoint)
        else:
            env.pop("EMFORGE_RESUME_FROM", None)
        return env

    def _observe(self, doc):
        rid = doc["run_id"]
        owned = self.owned[rid]
        if doc["desired"] == "stopped" and owned["stop_at"] is None:
            paths.runner_stop(self.root, rid).touch()
            owned["stop_at"] = time.monotonic()
            self._report(rid, "stopping")
        if owned["stop_at"] is not None and time.monotonic() - owned["stop_at"] >= self.stop_timeout_s:
            owned["process"].terminate()
        rc = owned["process"].poll()
        if rc is None:
            return
        state = "stopped" if owned["stop_at"] is not None else "completed" if rc == 0 else "failed"
        # 報告先落地，失敗下個 tick 可重報；不重新執行算法。
        self._report(rid, state, exit_code=rc, stdout=self._stdout(rid))
        self._release(rid)

    def _report(self, rid, state, **fields):
        return self.platform.call("run_report", run_id=rid, node=self.node, session=self.session, state=state, **fields)

    def _stdout(self, rid):
        p = paths.runner_stdout(self.root, rid)
        if not p.exists():
            return ""
        with p.open("rb") as stream:
            stream.seek(max(0, p.stat().st_size - 16384))
            return stream.read().decode("utf-8", errors="replace")

    def _release(self, rid):
        owned = self.owned.pop(rid)
        owned["process"].close()
        owned["out"].close()

    def _terminate(self, rid):
        self.owned[rid]["process"].terminate()
        self._release(rid)

    def close(self):
        if self._closed:
            return
        try:
            for rid in list(self.owned):
                self._terminate(rid)
                try:
                    self._report(rid, "interrupted", reason="算法執行端關閉；可從 checkpoint 建立續跑執行",
                                 stdout=self._stdout(rid))
                except Exception:
                    pass  # 網路不通時由下次節點接手標明 interrupted。
        finally:
            self.local.release(paths.runner_lock(), owner=self.session)
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
