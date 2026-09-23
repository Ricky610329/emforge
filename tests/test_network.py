"""真 socket 驗證 client、遠端 Depot 與驗證失敗。"""
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from emforge.client import Client
from emforge.platform.service import Platform
from emforge.platform.http_server import serving
from emforge.depot.http import HttpDepot
from emforge import testing
from emforge.runtime import collect
from tests.test_client import setup, patterns

def test_remote_client_and_worker_use_network_without_shared_disk(root):
    rt = setup(root)
    with serving(Platform(rt.depot), secret="test") as url:
        a = Client(url, "fake_f1", "anneal", run_id="run_a", token="test")
        sid = a.submit(patterns(2), request_id="request_one")
        assert a.submit(patterns(2), request_id="request_one") == sid
        remote = HttpDepot(url, token="test")
        for i in range(2):
            rt.tick()
            testing.run_all_jobs(root, depot=remote)
            collect.collect(rt)
            if i == 0:
                assert a.status(sid)["state"] == "dispatched_partial"
        assert a.status(sid)["state"] == "completed"
        assert len(a.results(sid)) == 2
        assert len([r for r in a.db.query() if r.kind == "sample"]) == 2   # 另有公證重測（I-21 後候選不再遺失）
        assert len(a.db.sample(1, seed=2)) == 1
        assert tuple(a.description["shape"]) == (8, 8)
        a.log("round", n=2)
        with pytest.raises(PermissionError):
            Client(url, "fake_f1", "anneal", token="bad").description

def test_http_claim_is_mutually_exclusive_and_wrong_owner_cannot_release(root):
    rt = setup(root)
    with serving(Platform(rt.depot)) as url:
        d = HttpDepot(url)
        barrier = threading.Barrier(8)
        def compete(i):
            barrier.wait(timeout=5)
            return d.claim("lease/x", {"owner": str(i)})
        with ThreadPoolExecutor(8) as pool:
            wins = list(pool.map(compete, range(8)))
        assert sum(wins) == 1
        assert d.release("lease/x", owner="wrong") is False
        assert d.owner("lease/x")["owner"] == str(wins.index(True))
        with pytest.raises(ValueError):
            d.list("../outside/")
        with pytest.raises(ValueError):
            d.ensure_prefixes(["../outside/"])

def test_submit_and_inbox_cli_over_network(root, tmp_path, capsys):
    import numpy as np
    from emforge.cli import main
    rt = setup(root)
    source = tmp_path / "patterns.npz"
    np.savez(source, patterns=patterns(1))
    with serving(Platform(rt.depot)) as url:
        assert main(["submit", "--endpoint", url, "--profile", "fake_f1", "--name", "anneal",
                     "--run-id", "run_cli", "--patterns", str(source)]) == 0
        assert capsys.readouterr().out.strip().startswith("s_")
        assert main(["inbox", "--endpoint", url, "--profile", "fake_f1"]) == 0
        assert "run_cli" in capsys.readouterr().out


def test_platform_overview_includes_idle_nodes_runtime_and_spec(root):
    from emforge.platform.service import Platform
    from emforge.platform.http_server import serving
    from emforge.platform.transport import RemotePlatform
    from tests.test_client import setup
    rt = setup(root)
    rt.write_status()
    with serving(Platform(rt.depot)) as url:
        remote = RemotePlatform(url)
        remote.call("node_heartbeat", node="idle_node", session="session", environments=["ant"], max_runs=1)
        state = remote.call("platform_state")
        assert state["nodes"][0]["node"] == "idle_node"
        assert not state["nodes"][0]["offline"]
        assert state["runtimes"]["fake_f1"]["profile"] == "fake_f1"
        assert state["fleet"] == []
        assert remote.call("description", profile="fake_f1")["spec_snapshot"]["aggregate"] == "min"


def test_priority_and_fairness_survive_http_depot_clients(root):
    from emforge.queue import Queue
    from tests.test_priority import add, finish
    rt = setup(root)
    with serving(Platform(rt.depot), secret="test") as url:
        q = Queue(HttpDepot(url, token="test"))
        add(q, "a0", strategy="alpha")
        add(q, "a1", strategy="alpha")
        add(q, "b0", strategy="beta")
        first = q.pick("216")
        assert first.store == "a0"
        finish(q, first)
        remote = Queue(HttpDepot(url, token="test"))
        assert remote.pick("218").store == "b0"
        add(q, "background", strategy="explore", prio=9)
        assert remote.raise_priority("background", 1)
        assert q.pick("37").store == "background"
