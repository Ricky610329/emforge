# -*- coding: utf-8 -*-
"""tests/runtime/conftest.py — runtime 測試共用：假 root＋strategies.yaml＋Runtime（in-process propose，快）。"""
import pytest

from emforge import paths, strategy, testing
from emforge.runtime.core import Runtime

YAML = """profile: fake_f1
runtime: {tick_s: 1, k_min: 3, noise_floor: 0.3, repeat_n: 2, quiet_s: 3600, strategy_error_limit: 3,
          propose_timeout_s: 30, notarize_prio: 1, background_prio: 9, max_error_rate: 0.5}
strategies:
  - {name: top_k_flip, prio: 3, batch: 4, params: {k: 2, d: 2}}
  - {name: blind, prio: 9, batch: 3}
"""


def write_yaml(root, text=YAML, profile="fake_f1"):
    y = paths.strategies_yaml(root, profile)
    y.parent.mkdir(parents=True, exist_ok=True)
    y.write_text(text, encoding="utf-8")
    return y


@pytest.fixture
def rt_root(root):
    testing.make_fake_root(root)
    write_yaml(root)
    return root


def make_rt(root, **kw):
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("propose_fn", strategy.propose_in_process)
    return Runtime(root, "fake_f1", **kw)


@pytest.fixture
def rt(rt_root):
    r = make_rt(rt_root)
    yield r
    r.release_lock()
