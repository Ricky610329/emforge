"""收件匣的提交、結果共用與中斷恢復。"""
import numpy as np
import pytest
from emforge import paths, testing, strategy
from emforge.client import Client
from emforge.runtime.core import Runtime
from emforge.runtime import collect

def setup(root, batch=2):
    testing.make_fake_root(root)
    text = f"""profile: fake_f1
strategies:
- {{name: anneal, kind: inbox, prio: 3, batch: {batch}}}
- {{name: search, kind: inbox, prio: 3, batch: {batch}}}
"""
    (root / paths.strategies_yaml("fake_f1")).parent.mkdir(parents=True, exist_ok=True)
    (root / paths.strategies_yaml("fake_f1")).write_text(text, encoding="utf-8")
    return Runtime(root, "fake_f1", propose_fn=strategy.propose_in_process)

def patterns(n):
    rng = np.random.default_rng(2)
    out = rng.random((n, 8, 8)) > .5
    out[:, testing.FAKE_PROFILE.fixed_on] = True
    return out

def test_duplicate_algorithms_both_get_results_and_submit_is_idempotent(root):
    rt = setup(root)
    a = Client(rt.depot, "fake_f1", "anneal", run_id="run_a")
    b = Client(rt.depot, "fake_f1", "search", run_id="run_b")
    x = patterns(1)
    sa = a.submit(x, request_id="request_a")
    assert a.submit(x, request_id="request_a") == sa
    with pytest.raises(ValueError):
        a.submit(patterns(2), request_id="request_a")
    sb = b.submit(x)
    rt.tick()
    assert len(rt.queue.list()) == 1
    testing.run_all_jobs(root)
    collect.collect(rt)
    assert a.status(sa)["state"] == b.status(sb)["state"] == "completed"
    assert a.results(sa)[0].id == b.results(sb)[0].id
    assert b.status(sb)["items"][0]["shared"]
    assert len(rt.db.metas("fake_f1")) == 1

def test_partial_dispatch_restarts_and_completed_cache_is_returned(root):
    rt = setup(root, batch=1)
    a = Client(rt.depot, "fake_f1", "anneal", run_id="run_a")
    sid = a.submit(patterns(3), tags=["a", "b", "c"])
    for i in range(3):
        rt.tick()
        testing.run_all_jobs(root)
        collect.collect(rt)
        rt = Runtime(root, "fake_f1", propose_fn=strategy.propose_in_process)
    assert a.status(sid)["state"] == "completed"
    assert len(a.results(sid)) == 3
    b = Client(rt.depot, "fake_f1", "search", run_id="run_b")
    other = b.submit(patterns(3))
    rt.tick()
    assert b.status(other)["state"] == "completed"
    assert all(x["shared"] for x in b.status(other)["items"])
    assert len([j for j in rt.queue.list() if j.origin == "inbox"]) == 3

def test_bad_submission_is_rejected_without_blocking_next_and_wait_is_bounded(root):
    rt = setup(root)
    a = Client(rt.depot, "fake_f1", "anneal", run_id="run_a")
    sid = a.submit(patterns(1))
    with pytest.raises(TimeoutError):
        a.wait(sid, timeout_s=0)
    doc = rt.depot.require_json(paths.submission("fake_f1", sid))
    doc["items"][0]["pattern"] = [2] * 64
    rt.depot.put_json(paths.submission("fake_f1", sid), doc)
    good = a.submit(patterns(2))
    rt.tick()
    rt.tick()  # 同時抵達的兩份送件不假設排序；每 tick 至多一批
    assert a.status(sid)["state"] == "rejected"
    assert a.status(good)["state"] != "rejected"

def test_dispatch_saved_but_status_lost_recovers_without_new_measurement(root, monkeypatch):
    rt = setup(root)
    a = Client(rt.depot, "fake_f1", "anneal", run_id="run_a")
    sid = a.submit(patterns(1))
    original = rt.depot.put_json
    def fail_status(key, data):
        if key == paths.submission_status("fake_f1", sid):
            raise OSError("模擬狀態寫入前中斷")
        return original(key, data)
    monkeypatch.setattr(rt.depot, "put_json", fail_status)
    rt.tick()                                            # 不穿出 tick（I-20）：事件 dispatch_failed，派出去的意圖照常對帳
    assert [e["name"] for e in rt.depot.read_log(paths.events_jsonl("fake_f1")) if e["event"] == "dispatch_failed"] == ["anneal"]
    monkeypatch.setattr(rt.depot, "put_json", original)
    testing.run_all_jobs(root)
    collect.collect(rt)
    rt = Runtime(root, "fake_f1", propose_fn=strategy.propose_in_process)
    rt.tick()
    assert a.status(sid)["state"] == "completed"
    assert len(rt.db.metas("fake_f1")) == 1
    assert not a.status(sid)["items"][0]["shared"]

def test_worker_done_before_runtime_collect_is_not_submission_complete(root):
    """回歸 I-5（2026-09-08）：worker 完成到 runtime 入庫間，client 不得提早回 completed。"""
    rt = setup(root)
    a = Client(rt.depot, "fake_f1", "anneal", run_id="run_a")
    sid = a.submit(patterns(1))
    rt.tick()
    testing.run_all_jobs(root)
    assert a.status(sid)["state"] == "dispatched"
    assert a.results(sid) == []
    collect.collect(rt)
    assert a.status(sid)["state"] == "completed" and len(a.results(sid)) == 1
