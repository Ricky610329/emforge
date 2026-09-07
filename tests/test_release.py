"""版本驗證、真行程切換與啟動失敗回退。"""
import shutil
import sys
import time
from pathlib import Path
import pytest
import emforge
from emforge.release.store import ReleaseStore
from emforge.release.supervisor import Supervisor
from emforge import paths, testing
from emforge.client import Client
from tests.test_client import patterns
from tests.test_client import setup

def source_tree(tmp_path):
    src = tmp_path / "candidate"
    shutil.copytree(Path(emforge.__file__).parent, src / "emforge", ignore=shutil.ignore_patterns("__pycache__"))
    (src / "tests").mkdir()
    (src / "tests" / "test_candidate.py").write_text("def test_import():\n import emforge\n assert emforge.__version__\n")
    return src

def wait_for(supervisor, predicate, timeout=20):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        supervisor.tick()
        if predicate(supervisor.state):
            return
        time.sleep(.05)
    raise AssertionError(supervisor.state)

def test_validation_isolated_and_tamper_refused(tmp_path):
    source = source_tree(tmp_path)
    store = ReleaseStore(tmp_path / "releases")
    version = store.stage(source, sys.executable)
    assert store.stage(source, sys.executable) == version
    with pytest.raises(ValueError):
        store.request(version)
    assert store.validate(version)["valid"], store.verify(version).get("checks")
    store.request(version)
    frozen = paths.release_source(store.root, version) / "emforge" / "__init__.py"
    frozen.write_text("# changed\n")
    with pytest.raises(ValueError, match="hash"):
        store.request(version)

def test_bad_validation_does_not_change_requested_release(tmp_path):
    source = source_tree(tmp_path)
    (source / "tests" / "test_candidate.py").write_text("def test_bad():\n assert False\n")
    store = ReleaseStore(tmp_path / "releases")
    version = store.stage(source, sys.executable)
    assert not store.validate(version)["valid"]
    with pytest.raises(ValueError):
        store.request(version)
    assert store.depot.get_json(paths.service_key("request")) is None

def test_true_process_upgrade_keeps_inflight_and_bad_start_rolls_back(root, tmp_path):
    rt = setup(root)
    # 讓 supervisor 測試快進，但使用真的 runtime tick 與 HTTP。
    key = paths.strategies_yaml("fake_f1")
    text = rt.depot.require_bytes(key).decode()
    rt.depot.put_bytes(key, (text+"runtime:\n  tick_s: 0.05\n").encode())
    source = source_tree(tmp_path)
    store = ReleaseStore(tmp_path / "releases")
    first = store.stage(source, sys.executable)
    assert store.validate(first)["valid"], store.verify(first).get("checks")
    store.request(first)
    with Supervisor(store.root, root, "fake_f1", port=0) as supervisor:
        wait_for(supervisor, lambda s: s["phase"] == "running")
        first_pid = supervisor.process.pid
        client = Client(supervisor.state["url"], "fake_f1", "anneal", run_id="during_upgrade")
        sid = client.submit(patterns(1), request_id="one")
        wait_for(supervisor, lambda s: client.status(sid)["state"] == "dispatched")
        inflight_stores = {j.store for j in rt.queue.list()}
        (source / "emforge" / "__init__.py").write_text((source / "emforge" / "__init__.py").read_text(encoding="utf-8")+"\n# next\n", encoding="utf-8")
        second = store.stage(source, sys.executable)
        assert store.validate(second)["valid"]
        store.request(second)
        wait_for(supervisor, lambda s: s["phase"] == "running" and s["current"] == second)
        assert supervisor.process.pid != first_pid
        assert supervisor.state["last_good"] == second
        assert inflight_stores <= {j.store for j in rt.queue.list()}
        testing.run_all_jobs(root)
        wait_for(supervisor, lambda s: client.status(sid)["state"] == "completed")
        assert len(client.results(sid)) == 1
        supervisor.process.terminate()
        wait_for(supervisor, lambda s: s["phase"] == "failed")
        store.request(second)
        wait_for(supervisor, lambda s: s["phase"] == "running")
        assert client.status(sid)["state"] == "completed"
        # import 與 fake probe 可過，但部署入口故障；必須回上一個可用版本。
        host = source / "emforge" / "release" / "host.py"
        host.write_text("raise RuntimeError('bad deployment entry')\n"+host.read_text(encoding="utf-8"), encoding="utf-8")
        bad = store.stage(source, sys.executable)
        assert store.validate(bad)["valid"]
        store.request(bad)
        wait_for(supervisor, lambda s: s["phase"] == "running" and s.get("rolled_back_from") == bad)
        assert supervisor.state["current"] == second
        assert "bad deployment entry" in supervisor.state["error"]
    assert not rt.depot.exists(paths.runtime_lock("fake_f1"))
    with Supervisor(store.root, root, "fake_f1", port=0) as restarted:
        wait_for(restarted, lambda s: s["phase"] == "running")
        assert restarted.state["current"] == second


def test_release_cli_stage_validate_apply_and_status(tmp_path, capsys):
    import json
    from emforge.cli import main
    source = source_tree(tmp_path)
    root = str(tmp_path / "releases")
    assert main(["release-stage", "--releases-root", root, "--source", str(source)]) == 0
    version = capsys.readouterr().out.strip()
    assert main(["release-validate", "--releases-root", root, "--version", version]) == 0
    capsys.readouterr()
    assert main(["release-apply", "--releases-root", root, "--version", version]) == 0
    capsys.readouterr()
    assert main(["release-status", "--releases-root", root]) == 0
    assert json.loads(capsys.readouterr().out)["request"]["version"] == version
