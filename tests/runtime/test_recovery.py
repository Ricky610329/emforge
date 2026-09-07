"""恢復已持久派工意圖，未知矛盾仍停機。"""
import numpy as np
import pytest
from emforge import paths, testing, strategy
from emforge.batches import Batch
from emforge.client import Client
from emforge.runtime import reconcile
from emforge.runtime.core import Runtime
from tests.test_client import setup, patterns

@pytest.mark.parametrize("boundary", ["patterns", "manifest", "job"])
def test_recover_partial_dispatch_without_duplicate(root, monkeypatch, boundary):
    rt = setup(root)
    c = Client(rt.depot, "fake_f1", "anneal", run_id="run_recover")
    sid = c.submit(patterns(1))
    put = rt.depot.put_bytes
    def fail(key, data):
        hit = (boundary == "patterns" and key.endswith("patterns.npz")
               or boundary == "manifest" and key.endswith("manifest.json")
               or boundary == "job" and key == paths.jobs_file())
        if hit:
            raise OSError("dispatch crash")
        return put(key, data)
    monkeypatch.setattr(rt.depot, "put_bytes", fail)
    with pytest.raises(OSError):
        rt.tick()
    monkeypatch.setattr(rt.depot, "put_bytes", put)
    other = Runtime(root, "fake_f1", propose_fn=strategy.propose_in_process)
    assert reconcile.recover(other) == []
    assert reconcile.reconcile(other) == []
    assert len(other.queue.list()) == 1
    assert reconcile.recover(other) == []
    testing.run_all_jobs(root)
    other.tick()
    assert c.status(sid)["state"] == "completed"
    assert len(c.results(sid)) == 1

def test_recovery_refuses_changed_pattern(root):
    rt = setup(root)
    Client(rt.depot, "fake_f1", "anneal", run_id="run_recover").submit(patterns(1))
    rt.tick()
    inf = rt.inflight()[0]
    batch = Batch(rt.depot, inf["store"])
    manifest = batch.manifest()
    rt.depot.delete(paths.batch_manifest(inf["store"]))
    batch.write(manifest, np.logical_not(patterns(1)), inf["ids"])
    assert reconcile.recover(rt)
    assert len(rt.queue.list()) == 1

def test_maintenance_collects_but_defers_all_new_dispatch(root):
    rt = setup(root)
    c = Client(rt.depot, "fake_f1", "anneal", run_id="run_drain")
    first = c.submit(patterns(1))
    rt.tick()
    rt.depot.put_json(paths.maintenance("fake_f1"), {"owner": "upgrade_a"})
    c.submit(patterns(2))
    testing.run_all_jobs(root)
    rt.tick()
    assert len(rt.queue.list()) == 1
    assert c.status(first)["state"] == "completed"
    assert rt.depot.require_json(paths.status_json("fake_f1"))["maintenance"]["owner"] == "upgrade_a"
    assert rt.state["notarize_deferred"]
    rt.depot.delete(paths.maintenance("fake_f1"))
    rt.tick()
    assert len(rt.queue.list()) > 1
