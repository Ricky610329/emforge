"""候選優先級：空閒填補、共享升級、同級公平與恢復。"""
import numpy as np
import pytest

from emforge import paths, strategy, testing
from emforge.client import Client
from emforge.runtime import collect, recovery
from emforge.runtime.core import Runtime
from tests.test_client import patterns


def setup(root, entries):
    testing.make_fake_root(root)
    text = "profile: fake_f1\nruntime: {repeat_n: 0}\nstrategies:\n"
    text += "\n".join("  - {" + entry + "}" for entry in entries)
    (root / paths.strategies_yaml("fake_f1")).parent.mkdir(parents=True, exist_ok=True)
    (root / paths.strategies_yaml("fake_f1")).write_text(text, encoding="utf-8")
    return Runtime(root, "fake_f1", propose_fn=strategy.propose_in_process)


def client(rt, name="search", run="run_a"):
    return Client(rt.depot, "fake_f1", name, run_id=run)


def test_pattern_priority_overrides_default_and_keeps_original_indices(root):
    rt = setup(root, ["name: search, kind: inbox, priority: background, max_inflight: 3"])
    c = client(rt)
    sid = c.submit(patterns(3), priorities=["background", "normal", "urgent"],
                   purposes=["補資料", "下一輪演化", "工程驗證"])
    rt.tick()
    job = rt.queue.pick("216")
    assert job.prio == 1 and job.n == 1
    status = c.status(sid)
    assert status["items"][2]["state"] == "dispatched"
    assert status["items"][0]["state"] == "received"
    assert status["items"][2]["purpose"] == "工程驗證"
    assert status["items"][2]["effective_prio"] == 1


def test_background_fills_while_foreground_running_and_reserves_one_pattern(root):
    rt = setup(root, ["name: search, kind: inbox, prio: 3, batch: 20",
                      "name: explore, kind: inbox, priority: background, batch: 20"])
    client(rt).submit(patterns(1))
    client(rt, "explore").submit(patterns(5)[1:])
    rt.tick()
    assert rt.queue.pick("216").prio == 3
    rt.tick()
    spare = rt.queue.pick("218")
    assert spare is not None and spare.prio == 9 and spare.n == 1


def test_same_pattern_promotes_queued_work_without_changing_dispatch_intent(root):
    rt = setup(root, ["name: explore, kind: inbox, priority: background",
                      "name: search, kind: inbox, priority: normal"])
    a, b = client(rt, "explore"), client(rt)
    sa = a.submit(patterns(1))
    rt.tick()
    first = rt.queue.list()[0]
    manifest = rt.depot.require_json(paths.batch_manifest(first.store))
    sb = b.submit(patterns(1), priority="urgent")
    rt.tick()
    assert len(rt.queue.list()) == 1
    assert rt.queue.list()[0].prio == 1
    assert rt.depot.require_json(paths.batch_manifest(first.store)) == manifest
    restored = Runtime(root, "fake_f1", propose_fn=strategy.propose_in_process)
    assert recovery.recover(restored) == []
    claimed = restored.queue.pick("216")
    assert claimed.prio == 1
    restored.queue.release(claimed.store, "216")
    testing.run_all_jobs(root)
    collect.collect(restored)
    assert a.status(sa)["state"] == b.status(sb)["state"] == "completed"
    assert a.results(sa)[0].id == b.results(sb)[0].id


def test_urgent_submission_is_not_blocked_by_same_strategy_background_limit(root):
    rt = setup(root, ["name: search, kind: inbox, priority: background, max_inflight: 1"])
    c = client(rt)
    c.submit(patterns(1))
    rt.tick()
    urgent = c.submit(patterns(2)[1:], priority="urgent")
    rt.tick()
    assert rt.queue.pick("216").prio == 1
    assert c.status(urgent)["state"] == "dispatched"


def test_reference_resolution_still_runs_at_inflight_limit(root):
    rt = setup(root, ["name: search, kind: inbox, prio: 3, max_inflight: 1"])
    c = client(rt)
    c.submit(patterns(1))
    rt.tick()
    sid = c.submit(patterns(1), priority="urgent")
    rt.tick()
    assert c.status(sid)["state"] == "dispatched"
    assert len(rt.queue.list()) == 1 and rt.queue.pick("216").prio == 1


def test_run_fairness_survives_runtime_restart(root):
    rt = setup(root, ["name: search, kind: inbox, priority: normal, max_inflight: 5"])
    a, b = client(rt, run="run_a"), client(rt, run="run_b")
    a.submit(patterns(5)[:3])
    rt.tick()
    sb = b.submit(patterns(5)[3:])
    rt = Runtime(root, "fake_f1", propose_fn=strategy.propose_in_process)
    rt.tick()
    assert b.status(sb)["state"] != "received", "大批舊送件不得堵住另一個 run"
    assert all(j.n == 1 for j in rt.queue.list())


def test_priority_does_not_change_dedup_id_and_is_in_idempotency_payload(root):
    rt = setup(root, ["name: search, kind: inbox, priority: normal"])
    c = client(rt)
    sid = c.submit(patterns(1), priority="normal", request_id="round_a")
    assert c.submit(patterns(1), priority="normal", request_id="round_a") == sid
    with pytest.raises(ValueError):
        c.submit(patterns(1), priority="urgent", request_id="round_a")
    with pytest.raises(ValueError):
        c.submit(patterns(1), priority="urgnet")
    with pytest.raises(ValueError):
        c.submit(patterns(1), priorities=["normal", "urgent"])


def test_proposal_priority_survives_subprocess_wire(root):
    p = strategy.validate_proposals([dict(pattern=patterns(1)[0], priority="urgent", purpose="演化")],
                                    testing.FAKE_PROFILE, 1)[0]
    path = root / "proposal.npz"
    strategy._write_proposals(path, [p], testing.FAKE_PROFILE.shape)
    reread = strategy._read_proposals(path)[0]
    assert reread.priority == "urgent" and reread.purpose == "演化"
    assert np.array_equal(reread.pattern, p.pattern)


def test_legacy_strategy_mixed_priorities_dispatches_each_pattern_without_loss(root):
    rt = setup(root, ["name: search, prio: 3, batch: 3"])
    source = (
        "COMPATIBLE = {'*'}\n"
        "def propose(ctx):\n"
        "    out = []\n"
        "    for level in ['background', 'urgent', 'normal']:\n"
        "        p = ctx.rng.random(ctx.profile.shape) > .5\n"
        "        p[ctx.profile.fixed_on] = True\n"
        "        out.append(dict(pattern=p, priority=level))\n"
        "    return out\n"
    )
    (paths.user_strategies_dir(root) / "search.py").write_text(source, encoding="utf-8")
    rt.tick()
    assert [j.prio for j in rt.queue.list()] == [1, 3, 9]
    assert len({j.store for j in rt.queue.list()}) == 3
    assert recovery.recover(rt) == []
    testing.run_all_jobs(root)
    collect.collect(rt)
    assert len(rt.db.ids("fake_f1")) == 3


def test_invalid_shared_submission_cannot_raise_existing_priority(root):
    rt = setup(root, ["name: search, kind: inbox, priority: background"])
    c = client(rt)
    c.submit(patterns(1))
    rt.tick()
    bad = c.submit(patterns(1), priority="urgent")
    doc = rt.depot.require_json(paths.submission("fake_f1", bad))
    doc["items"][0]["pattern"] = [2] * 64
    rt.depot.put_json(paths.submission("fake_f1", bad), doc)
    rt.tick()
    assert c.status(bad)["state"] == "rejected"
    assert rt.queue.list()[0].prio == 9


def test_three_background_workers_fill_without_unbounded_prefetch(root):
    rt = setup(root, ["name: explore, kind: inbox, priority: background, max_inflight: 3"])
    c = client(rt, "explore")
    c.submit(patterns(8))
    for machine in ("216", "218", "37"):
        rt.tick()
        job = rt.queue.pick(machine)
        assert job is not None and job.prio == 9 and job.n == 1
    rt.tick()
    assert len(rt.queue.list()) == 3
    assert all(rt.queue.state(j.store) == "claimed" for j in rt.queue.list())
