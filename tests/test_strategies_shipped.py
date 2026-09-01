# -*- coding: utf-8 -*-
"""tests/test_strategies_shipped.py — 內建策略 `blind`／`top_k_flip`。

內建策略是「平台幫使用者做得更好」的示範，不是平台的一部分；但它們必須遵守自己訂的契約。
"""
import numpy as np

from emforge import db as dbm
from emforge import model, strategy, testing

P = testing.FAKE_PROFILE


def _ctx(root, name, budget, seed, params=None):
    return strategy.make_context(root, P, name, budget=budget, seed=seed, tick=1, params=params or {})


def test_shipped_strategies_declare_wildcard_compatible(root):
    for name in ("blind", "top_k_flip"):
        mod = strategy.load_strategy(strategy.resolve_strategy_path(root, name))
        assert mod.COMPATIBLE == {"*"}


def test_blind_deterministic_both_ways_respects_fixed_on_budget_arm_blind(root):
    testing.make_fake_root(root)
    a = strategy.propose_in_process(root, P, "blind", budget=6, seed=3, tick=1, params={})
    b = strategy.propose_in_process(root, P, "blind", budget=6, seed=3, tick=1, params={})
    c = strategy.propose_in_process(root, P, "blind", budget=6, seed=4, tick=1, params={})
    assert len(a) == 6 and all(p.arm == model.ARM_BLIND and p.parent is None for p in a)
    assert all(np.array_equal(x.pattern, y.pattern) for x, y in zip(a, b))
    assert any(not np.array_equal(x.pattern, y.pattern) for x, y in zip(a, c))
    assert all(p.pattern[P.fixed_on].all() for p in a), "饋墊永遠金屬"
    assert len({model.record_id(p.pattern, P.name) for p in a}) == 6, "批內不重複"


def test_top_k_flip_empty_on_empty_db(root):
    testing.make_fake_root(root)
    assert strategy.propose_in_process(root, P, "top_k_flip", budget=5, seed=0, tick=1, params={}) == []


def _seed(root, n):
    d = dbm.Database(root, write_profile=P.name)
    rng = np.random.default_rng(0)
    for i in range(n):
        b = rng.random(P.shape) > 0.5
        b[P.fixed_on] = True
        d.add(model.Record(id=model.record_id(b, P.name), sim_profile=P.name, bits=b,
                           response=np.zeros((2, 17), np.float32), measure={"m1": -float(i)}, score=-float(i),
                           status="done", strategy="blind", arm="blind", parent=None, tick=0, seed=0, note={},
                           kind="sample", run={"store": "s", "machine": "x", "worker_ver": "v", "profile_hash": "h",
                                              "time_s": 1.0}))


def test_top_k_flip_flips_exactly_d_free_pixels_sets_parent(root):
    testing.make_fake_root(root)
    _seed(root, 5)
    view = dbm.Database(root).view(P.name)
    top = {r.id: r for r in view.top(2)}
    out = strategy.propose_in_process(root, P, "top_k_flip", budget=6, seed=1, tick=1, params={"k": 2, "d": 3})
    assert len(out) == 6
    for p in out:
        assert p.parent in top, "parent 指向 top-k 之一"
        diff = p.pattern ^ top[p.parent].bits
        assert diff.sum() == 3, "恰翻 d 格"
        assert not diff[P.fixed_on].any(), "不翻饋墊"
    a = strategy.propose_in_process(root, P, "top_k_flip", budget=6, seed=1, tick=1, params={"k": 2, "d": 3})
    assert all(np.array_equal(x.pattern, y.pattern) for x, y in zip(a, out)), "同 seed 同輸出"
