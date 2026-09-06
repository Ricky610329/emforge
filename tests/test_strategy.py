# -*- coding: utf-8 -*-
"""tests/test_strategy.py — `emforge/strategy.py`：策略載入、COMPATIBLE、Proposal 驗證、Context、子行程 propose、yaml。

防什麼：I-6（策略端雙邊宣告）、I-4（策略例外殺死主行程）、D7（策略塞 runtime 欄位）、yaml 靜默吃錯鍵。
"""
import textwrap
import time

import numpy as np
import pytest

from emforge import model, paths, strategy, testing

GOOD = textwrap.dedent("""
    COMPATIBLE = {"fake_f1"}
    def propose(ctx):
        pat = ctx.rng.random(ctx.profile.shape) < 0.5
        pat[ctx.profile.fixed_on] = True
        return [dict(pattern=pat, note={"tick": ctx.tick, "k": ctx.params.get("k")}) for _ in range(ctx.budget)]
""")


def _write(root, name, src):
    d = paths.user_strategies_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.py"
    p.write_text(textwrap.dedent(src), encoding="utf-8")
    return p


# ── 載入與相容 ──────────────────────────────────────────────────────────────
def test_load_requires_compatible_set_and_propose_callable(root):
    with pytest.raises(strategy.StrategyFailure, match="COMPATIBLE"):
        strategy.load_strategy(_write(root, "no_compat", "def propose(ctx): return []"))
    with pytest.raises(strategy.StrategyFailure, match="propose"):
        strategy.load_strategy(_write(root, "no_propose", "COMPATIBLE = {'fake_f1'}\npropose = 3"))
    with pytest.raises(strategy.StrategyFailure, match="COMPATIBLE"):
        strategy.load_strategy(_write(root, "bad_compat", "COMPATIBLE = 'fake_f1'\ndef propose(ctx): return []"))
    mod = strategy.load_strategy(_write(root, "good", GOOD))
    assert callable(mod.propose) and mod.COMPATIBLE == {"fake_f1"}


def test_check_compatible_rejects_undeclared_profile_accepts_wildcard(root):
    """回歸 I-6（2026-08-31）雙邊宣告的策略端：策略沒說它懂這個 profile 就不准載進這個實例。"""
    mod = strategy.load_strategy(_write(root, "dual_only", "COMPATIBLE = {'dual_p01_db075'}\ndef propose(ctx): return []"))
    with pytest.raises(strategy.StrategyFailure, match="COMPATIBLE"):
        strategy.check_compatible(mod, "fake_f1")
    strategy.check_compatible(mod, "dual_p01_db075")
    anym = strategy.load_strategy(_write(root, "anyp", "COMPATIBLE = {'*'}\ndef propose(ctx): return []"))
    strategy.check_compatible(anym, "fake_f1")


def test_resolve_user_dir_shadows_shipped_and_unknown_raises(root):
    shipped = strategy.resolve_strategy_path(root, "blind")
    assert shipped.parent.name == "strategies" and "emforge" in shipped.parts
    user = _write(root, "blind", GOOD)
    assert strategy.resolve_strategy_path(root, "blind") == user, "使用者目錄蓋過內建"
    with pytest.raises(strategy.StrategyFailure, match="nope"):
        strategy.resolve_strategy_path(root, "nope")
    with pytest.raises(strategy.StrategyFailure, match="保留"):
        strategy.resolve_strategy_path(root, "notarize")


# ── Proposal 驗證 ───────────────────────────────────────────────────────────
def test_validate_rejects_shape_dtype_fixed_on_budget_and_forbidden_keys():
    p = testing.FAKE_PROFILE
    ok = np.zeros(p.shape, bool)
    ok[p.fixed_on] = True
    assert len(strategy.validate_proposals([dict(pattern=ok)], p, budget=3)) == 1
    with pytest.raises(model.ProposalError, match="shape"):
        strategy.validate_proposals([dict(pattern=np.zeros((3, 3), bool))], p, budget=3)
    with pytest.raises(model.ProposalError, match="0/1"):
        strategy.validate_proposals([dict(pattern=np.full(p.shape, 0.5))], p, budget=3)
    bad_feed = ok.copy()
    bad_feed[0, 0] = False
    with pytest.raises(model.ProposalError, match="fixed_on"):
        strategy.validate_proposals([dict(pattern=bad_feed)], p, budget=3)
    with pytest.raises(model.ProposalError, match="budget"):
        strategy.validate_proposals([dict(pattern=ok)] * 4, p, budget=3)
    with pytest.raises(model.ProposalError, match="kind"):
        strategy.validate_proposals([dict(pattern=ok, kind="repeat")], p, budget=3)
    with pytest.raises(model.ProposalError, match="list"):
        strategy.validate_proposals(dict(pattern=ok), p, budget=3)


def test_validate_accepts_dict_or_proposal_and_casts_float01_to_bool():
    p = testing.FAKE_PROFILE
    f = np.zeros(p.shape, np.float32)
    f[p.fixed_on] = 1.0
    out = strategy.validate_proposals([dict(pattern=f), model.Proposal(pattern=f > 0.5, arm="x")], p, budget=2)
    assert out[0].pattern.dtype == bool and out[1].arm == "x"
    assert strategy.validate_proposals([], p, budget=5) == []


# ── yaml ────────────────────────────────────────────────────────────────────
YAML = """
profile: fake_f1
runtime: {tick_s: 5, k_min: 3}
strategies:
  - {name: top_k_flip, prio: 3, batch: 6, params: {k: 2, d: 1}}
  - {name: blind, prio: 9, batch: 4}
"""


def test_load_yaml_defaults_max_inflight_1_reads_runtime_block_rejects_unknown_keys(root):
    y = root / paths.strategies_yaml("fake_f1")
    y.parent.mkdir(parents=True)
    y.write_text(YAML, encoding="utf-8")
    cfg = strategy.load_strategies_yaml(y)
    assert cfg.profile == "fake_f1" and cfg.runtime.tick_s == 5 and cfg.runtime.k_min == 3
    assert cfg.runtime.background_prio == 9 and cfg.runtime.strategy_error_limit == 3, "沒寫的用預設"
    a, b = cfg.strategies
    assert (a.name, a.prio, a.batch, a.max_inflight, a.enabled, a.params) == ("top_k_flip", 3, 6, 1, True, {"k": 2, "d": 1})
    assert b.params == {} and b.seed is None
    y.write_text(YAML.replace("batch: 4", "batch: 4, batchh: 5"), encoding="utf-8")
    with pytest.raises(strategy.ConfigError, match="batchh"):
        strategy.load_strategies_yaml(y)
    y.write_text(YAML.replace("runtime: {tick_s: 5, k_min: 3}", "runtime: {tick_s: 5, kmin: 3}"), encoding="utf-8")
    with pytest.raises(strategy.ConfigError, match="kmin"):
        strategy.load_strategies_yaml(y)


def test_yaml_profile_mismatch_duplicate_and_reserved_names_rejected(root):
    y = root / paths.strategies_yaml("fake_f1")
    y.parent.mkdir(parents=True)
    y.write_text(YAML, encoding="utf-8")
    with pytest.raises(strategy.ConfigError, match="profile"):
        strategy.load_strategies_yaml(y, profile="other_p")
    y.write_text(YAML.replace("name: blind", "name: top_k_flip"), encoding="utf-8")
    with pytest.raises(strategy.ConfigError, match="重複"):
        strategy.load_strategies_yaml(y)
    y.write_text(YAML.replace("name: blind", "name: notarize"), encoding="utf-8")
    with pytest.raises(strategy.ConfigError, match="保留"):
        strategy.load_strategies_yaml(y)
    y.write_text(YAML.replace("name: blind", "name: Bad-Name"), encoding="utf-8")
    with pytest.raises(strategy.ConfigError, match="Bad-Name"):
        strategy.load_strategies_yaml(y)


# ── Context 與 propose ──────────────────────────────────────────────────────
def test_make_context_binds_view_rng_workdir_params(root):
    testing.make_fake_root(root)
    ctx = strategy.make_context(root, testing.FAKE_PROFILE, "top_k_flip", budget=7, seed=11, tick=3, params={"k": 2})
    assert ctx.budget == 7 and ctx.tick == 3 and ctx.params == {"k": 2} and ctx.profile is testing.FAKE_PROFILE
    assert ctx.workdir == paths.strategy_workdir(root, "fake_f1", "top_k_flip") and ctx.workdir.is_dir()
    assert ctx.db.profile == "fake_f1" and ctx.db.mine() == [] and not hasattr(ctx.db, "add")
    a = ctx.rng.random()
    b = strategy.make_context(root, testing.FAKE_PROFILE, "top_k_flip", budget=7, seed=11, tick=3, params={}).rng.random()
    assert a == b, "同 seed 同 rng"


def test_propose_in_process_runs_user_strategy(root):
    testing.make_fake_root(root)
    _write(root, "good", GOOD)
    out = strategy.propose_in_process(root, testing.FAKE_PROFILE, "good", budget=4, seed=1, tick=2, params={"k": 9})
    assert len(out) == 4 and all(isinstance(p, model.Proposal) for p in out)
    assert out[0].note == {"tick": 2, "k": 9} and out[0].pattern[testing.FAKE_PROFILE.fixed_on].all()


def test_propose_in_subprocess_returns_proposals_deterministically(root):
    testing.make_fake_root(root)
    kw = dict(budget=5, seed=7, tick=1, params={}, timeout_s=60)
    a = strategy.propose_in_subprocess(root, testing.FAKE_PROFILE, "blind", **kw)
    b = strategy.propose_in_subprocess(root, testing.FAKE_PROFILE, "blind", **kw)
    assert len(a) == 5 and all(p.arm == "blind" for p in a)
    assert all(np.array_equal(x.pattern, y.pattern) for x, y in zip(a, b)), "同 seed 同輸出"
    c = strategy.propose_in_subprocess(root, testing.FAKE_PROFILE, "blind", **{**kw, "seed": 8})
    assert not np.array_equal(a[0].pattern, c[0].pattern), "異 seed 不同"


def test_child_exception_becomes_strategy_error_parent_alive(root):
    """回歸 I-4（2026-08-03）：策略程式碼炸了殺掉整個 worker。策略在子行程跑，例外變成父行程可捕捉的 StrategyFailure。"""
    testing.make_fake_root(root)
    _write(root, "boom", "COMPATIBLE = {'*'}\ndef propose(ctx):\n    raise RuntimeError('kaboom 炸了')")
    with pytest.raises(strategy.StrategyFailure, match="kaboom"):
        strategy.propose_in_subprocess(root, testing.FAKE_PROFILE, "boom", budget=1, seed=0, tick=0, params={}, timeout_s=60)
    _write(root, "badout", "COMPATIBLE = {'*'}\ndef propose(ctx):\n    return [dict(pattern=ctx.profile.fixed_on, kind='repeat')]")
    with pytest.raises(strategy.StrategyFailure, match="kind"):
        strategy.propose_in_subprocess(root, testing.FAKE_PROFILE, "badout", budget=1, seed=0, tick=0, params={}, timeout_s=60)


def test_timeout_kills_child_raises_strategy_timeout(root):
    testing.make_fake_root(root)
    _write(root, "slow", "import time\nCOMPATIBLE = {'*'}\ndef propose(ctx):\n    time.sleep(30)\n    return []")
    t0 = time.time()
    with pytest.raises(strategy.StrategyTimeout):
        strategy.propose_in_subprocess(root, testing.FAKE_PROFILE, "slow", budget=1, seed=0, tick=0, params={}, timeout_s=2)
    assert time.time() - t0 < 15
