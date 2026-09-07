"""算法執行身分與歷程。"""
import dataclasses
import numpy as np
import pytest
from emforge import db, model, strategy, testing
from emforge.depot import MemoryDepot
from tests.test_db import _rec

def test_identity_roundtrip_and_old_record():
    testing.register_fakes()
    p = model.Proposal(np.ones((8, 8), bool), tag="flip_k3", run_id="run_a")
    assert strategy.validate_proposals([p], testing.FAKE_PROFILE, 1)[0].run_id == "run_a"
    r = dataclasses.replace(_rec(1, "s1"), tag="flip_k3", run_id="run_a")
    back = model.Record.from_meta(r.meta(), r.bits, r.response)
    assert back.run_id == "run_a" and back.run["store"] == "s1"
    old = r.meta()
    for key in ("tag", "run_id"):
        old.pop(key)
    assert model.Record.from_meta(old, r.bits, r.response).run_id is None
    with pytest.raises(model.ProposalError):
        strategy.validate_proposals([dataclasses.replace(p, run_id="../escape")], testing.FAKE_PROFILE, 1)

def test_lineage_ignores_repeat_self_parent_and_sample_is_deterministic():
    d = db.Database(MemoryDepot())
    a = dataclasses.replace(_rec(1, "s1"), run_id="run_a", tag="seed")
    b = dataclasses.replace(_rec(2, "s2"), parent=a.id, run_id="run_b")
    c = dataclasses.replace(_rec(3, "s3"), parent=b.id, run_id="run_b")
    repeat = dataclasses.replace(c, kind="repeat", parent=c.id, run={"store": "repeat1"})
    for r in (a, b, c, repeat):
        d.add(r)
    v = d.view("fake_f1", strategy="s")
    assert [r.id for r in v.lineage(c.id)] == [c.id, b.id, a.id]
    assert [r.id for r in v.children(a.id)] == [b.id]
    assert {r.id for r in v.mine(run_id="run_b")} == {b.id, c.id}
    assert v.runs() == ["run_a", "run_b"]
    assert [r.id for r in v.sample(2, seed=3)] == [r.id for r in v.sample(2, seed=3)]
    assert len(v.sample(99, seed=4)) == 3
    assert len(v.query(tag="seed")) == 1

def test_identity_survives_real_strategy_subprocess(root):
    from emforge import paths
    testing.make_fake_root(root)
    path = paths.user_strategies_dir(root) / "identity.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('import numpy as np\nCOMPATIBLE={"*"}\ndef propose(ctx):\n'
                    ' return [dict(pattern=np.ones(ctx.profile.shape, bool), tag="seed", run_id="run_a")]\n',
                    encoding="utf-8")
    props = strategy.propose_in_subprocess(root, testing.FAKE_PROFILE, "identity", budget=1, seed=1,
                                           tick=1, params={}, timeout_s=20)
    assert props[0].run_id == "run_a" and props[0].tag == "seed"

def test_dispatch_collect_and_notarize_preserve_identity(root):
    from emforge.runtime import dispatch, collect, notarize
    from tests.runtime.conftest import write_yaml, make_rt
    testing.make_fake_root(root)
    write_yaml(root)
    rt = make_rt(root)
    p = model.Proposal(np.ones((8, 8), bool), tag="seed", run_id="run_a")
    store = dispatch.dispatch(rt, "blind", [p], tick=1, seed=1, prio=1)
    testing.run_all_jobs(root)
    rows = collect.collect(rt)
    assert len(rows) == 1 and rows[0].run_id == "run_a" and rows[0].run["store"] == store
    notarize.notarize_step(rt, rows)
    assert all(item["run_id"] == "run_a" for inf in rt.inflight() for item in inf["items"].values())
