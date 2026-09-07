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
        rt.tick()
        remote = HttpDepot(url, token="test")
        testing.run_all_jobs(root, depot=remote)
        collect.collect(rt)
        assert a.status(sid)["state"] == "completed"
        assert len(a.results(sid)) == 2
        assert len(a.db.query()) == 2
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
