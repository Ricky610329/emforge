"""算法節點的固定版本、指定機器、環境與生命週期。"""
import sys
import time
import pytest
from emforge.platform.service import Platform
from emforge.platform.http_server import serving
from emforge.runner.node import Runner
from tests.test_client import setup

CODE = """import os, json, numpy as np
from emforge.client import Client
c = Client(os.environ['EMFORGE_ENDPOINT'], os.environ['EMFORGE_PROFILE'],
           os.environ['EMFORGE_ALGORITHM'], run_id=os.environ['EMFORGE_RUN_ID'])
p = np.ones(c.description['shape'], bool)
sid = c.submit([p], request_id='round_one')
c.log('submitted', sid=sid)
"""

def register(service, name="anneal", code=CODE, requires=()):
    return service.call("algorithm_register", name=name, files={"main.py": code},
                        entrypoint="main.py", requires=list(requires))

def start(service, version, run_id="run_a", node="gpu_a", name="anneal"):
    return service.call("run_start", name=name, version=version, run_id=run_id, node=node,
                        environment="ant", profile="fake_f1", params={}, seed=1, budget=10)


def test_run_name_cannot_collide_with_profile_config_lock(root, monkeypatch):
    """回歸 I-3（2026-09-12）：防止合法 run_id 與內部設定鎖撞名而自我等待。"""
    service = Platform(setup(root).depot)
    version = register(service, code="pass")
    service.call("node_heartbeat", node="gpu_a", session="test", environments=["ant"])
    original = service.depot.lock
    monkeypatch.setattr(service.depot, "lock", lambda key, **kw: original(key, timeout_s=.05, **kw))
    assert start(service, version, run_id="config_fake_f1")["state"] == "queued"
    assert start(service, version, run_id="node_gpu_a")["state"] == "queued"

def poll(runner, service, run_id, wanted, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        runner.tick()
        info = service.call("run_status", run_id=run_id)
        if info["state"] in wanted:
            return info
        time.sleep(.03)
    raise AssertionError(service.call("run_status", run_id=run_id))

def test_fixed_package_launch_idempotency_and_env_check(root, tmp_path):
    rt = setup(root)
    s = Platform(rt.depot)
    version = register(s)
    assert register(s) == version
    with serving(s) as url, Runner(tmp_path / "node", url, "gpu_a", {"ant": sys.executable}) as runner:
        runner.tick()
        start(s, version)
        assert start(s, version)["run_id"] == "run_a"
        assert poll(runner, s, "run_a", {"completed"})["exit_code"] == 0
        assert len(s.call("inbox_list", profile="fake_f1")) == 1
        assert s.call("run_logs", run_id="run_a")["algorithm"][0]["event"] == "submitted"
        v2 = register(s, code="pass", requires=["missing_package_for_emforge_test"])
        start(s, v2, run_id="run_bad")
        bad = poll(runner, s, "run_bad", {"failed"})
        assert "missing_package" in bad["reason"]
        with pytest.raises(ValueError):
            start(s, v2)  # 同 run_id 不可換內容

def test_stop_only_owned_run_and_new_version_new_run(root, tmp_path):
    rt = setup(root)
    s = Platform(rt.depot)
    version = register(s, code="import time\nwhile True: time.sleep(.05)\n")
    with serving(s) as url, Runner(tmp_path / "node", url, "gpu_a", {"ant": sys.executable},
                                  max_runs=2, stop_timeout_s=.1) as runner:
        runner.tick()
        start(s, version)
        start(s, version, run_id="run_b")
        poll(runner, s, "run_a", {"running"})
        s.call("run_stop", run_id="run_a")
        assert poll(runner, s, "run_a", {"stopped"})["state"] == "stopped"
        assert s.call("run_status", run_id="run_b")["state"] == "running"

def test_package_paths_cannot_escape_and_unknown_node_rejected(root):
    s = Platform(setup(root).depot)
    with pytest.raises(ValueError):
        s.call("algorithm_register", name="anneal", files={"../bad.py": "pass"}, entrypoint="../bad.py")
    v = register(s)
    with pytest.raises(ValueError):
        start(s, v)

def test_run_budget_and_fixed_spec_are_enforced(root, tmp_path):
    import numpy as np
    from emforge.client import Client
    from emforge.runtime import collect
    from emforge import testing
    rt = setup(root)
    s = Platform(rt.depot)
    version = register(s, code="pass")
    with serving(s) as url, Runner(tmp_path / "node", url, "gpu_a", {"ant": sys.executable}) as runner:
        runner.tick()
        s.call("run_start", name="anneal", version=version, run_id="limited", node="gpu_a",
               environment="ant", profile="fake_f1", budget=1)
        client = Client(url, "fake_f1", "anneal", run_id="limited")
        x = np.ones((2, 8, 8), bool)
        x[1].reshape(-1)[np.flatnonzero(~testing.FAKE_PROFILE.fixed_on.reshape(-1))[0]] = False
        sid = client.submit(x)
        rt.tick()
        testing.run_all_jobs(root)
        collect.collect(rt)
        rt.tick()
        status = client.status(sid)
        assert status["state"] == "completed"
        assert sorted(i["state"] for i in status["items"]) == ["done", "rejected"]
        assert len([r for r in rt.db.metas("fake_f1") if r["kind"] == "sample"]) == 1

def test_node_shutdown_preserves_checkpoint_and_explicit_resume(root, tmp_path):
    rt = setup(root)
    s = Platform(rt.depot)
    version = s.call("algorithm_register", name="anneal", files={"main.py": "pass"},
                     entrypoint="main.py", checkpoint_schema="v1")
    with serving(s) as url:
        with Runner(tmp_path / "node", url, "gpu_a", {"ant": sys.executable}) as runner:
            runner.tick()
            start(s, version)
            poll(runner, s, "run_a", {"completed"})
        with Runner(tmp_path / "node", url, "gpu_a", {"ant": sys.executable}) as runner:
            runner.tick()
            doc = s.call("run_start", name="anneal", version=version, run_id="run_b", node="gpu_a",
                         environment="ant", profile="fake_f1", resume_from="run_a")
            assert doc["identity"]["resume_from"] == "run_a"
            assert poll(runner, s, "run_b", {"completed"})["exit_code"] == 0

def test_two_algorithm_nodes_and_two_simulators_complete_three_rounds(root, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from emforge import testing
    from emforge.depot.http import HttpDepot
    from emforge.runtime import collect
    rt = setup(root)
    service = Platform(rt.depot)
    code = """import os, numpy as np
from emforge.client import Client
c=Client(os.environ['EMFORGE_ENDPOINT'], os.environ['EMFORGE_PROFILE'],
         os.environ['EMFORGE_ALGORITHM'], run_id=os.environ['EMFORGE_RUN_ID'])
rng=np.random.default_rng(int(os.environ['EMFORGE_SEED']))
desc=c.description
for i in range(3):
    p=rng.random(desc['shape'])>.5
    p[np.asarray(desc['fixed_on'],bool)]=True
    sid=c.submit([p],request_id='round_'+str(i))
    c.wait(sid,timeout_s=25,poll_s=.05)
    assert len(c.results(sid))==1
    c.log('round',index=i)
"""
    version_a = register(service, "anneal", code)
    version_b = register(service, "search", code)
    simroots = [tmp_path / "sim_a", tmp_path / "sim_b"]
    for r in simroots:
        testing.make_fake_root(r, depot=rt.depot)
    with serving(service) as url, Runner(tmp_path / "a", url, "gpu_a", {"ant": sys.executable}) as a, \
            Runner(tmp_path / "b", url, "gpu_b", {"ant": sys.executable}) as b, ThreadPoolExecutor(2) as pool:
        a.tick()
        b.tick()
        start(service, version_a)
        service.call("run_start", name="search", version=version_b, run_id="run_b", node="gpu_b",
                     environment="ant", profile="fake_f1", seed=2, budget=5)
        deadline = time.monotonic()+25
        while time.monotonic()<deadline:
            a.tick()
            b.tick()
            rt.tick()
            jobs = [pool.submit(testing.run_all_jobs, r, machine_tag=str(i), depot=HttpDepot(url))
                    for i,r in enumerate(simroots)]
            for job in jobs:
                job.result(timeout=10)
            collect.collect(rt)
            if all(service.call("run_status",run_id=r)["state"]=="completed" for r in ("run_a","run_b")):
                break
            time.sleep(.03)
        assert [service.call("run_status",run_id=r)["state"] for r in ("run_a","run_b")] == ["completed","completed"]
        for r in ("run_a","run_b"):
            assert len(service.call("run_logs",run_id=r)["algorithm"]) == 3
        assert len([m for m in rt.db.metas("fake_f1") if m["kind"]=="sample"]) == 6

def test_algorithm_cli_register_start_stop(root, tmp_path, capsys):
    from emforge.cli import main
    rt=setup(root)
    source=tmp_path / "code"
    source.mkdir()
    (source / "main.py").write_text("pass",encoding="utf-8")
    with serving(Platform(rt.depot)) as url, Runner(tmp_path / "node",url,"gpu_a",{"ant":sys.executable}) as runner:
        runner.tick()
        assert main(["algorithm-register","--endpoint",url,"--name","anneal","--source",str(source)]) == 0
        version=capsys.readouterr().out.strip()
        assert version.startswith("pkg_")
        assert main(["algorithm-start","--endpoint",url,"--name","anneal","--version",version,
                     "--run-id","cli_run","--node","gpu_a","--environment","ant","--profile","fake_f1"]) == 0
        assert main(["algorithm-stop","--endpoint",url,"--run-id","cli_run"]) == 0


def test_hosted_anneal_example_three_rounds(root, tmp_path):
    import json
    from pathlib import Path
    import emforge
    from emforge import testing, paths
    from emforge.runtime import collect
    rt = setup(root)
    service = Platform(rt.depot)
    source = Path(emforge.__file__).parent.parent / "examples" / "anneal" / "main.py"
    version = register(service, code=source.read_text(encoding="utf-8"))
    with serving(service) as url, Runner(tmp_path / "node", url, "gpu_a", {"ant": sys.executable}) as runner:
        runner.tick()
        service.call("run_start", name="anneal", version=version, run_id="example_run", node="gpu_a",
                     environment="ant", profile="fake_f1", budget=5, params={"rounds": 3})
        deadline = time.monotonic()+20
        while time.monotonic() < deadline:
            runner.tick()
            rt.tick()
            testing.run_all_jobs(root)
            collect.collect(rt)
            state = service.call("run_status", run_id="example_run")
            if state["state"] in ("completed", "failed"):
                break
            time.sleep(.03)
        assert state["state"] == "completed", state
        work = paths.runner_work(tmp_path / "node", "example_run")
        assert json.loads((work / "checkpoint.json").read_text())["step"] == 3
        assert len(service.call("run_logs", run_id="example_run")["algorithm"]) == 3
