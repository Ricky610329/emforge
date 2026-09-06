# -*- coding: utf-8 -*-
"""tests/device/conftest.py — 儀器測試共用：MemoryDepot＋假根＋FakeSimulator 工廠＋可注入時鐘的 Instrument。"""
import numpy as np
import pytest

from emforge import testing
from emforge.depot import MemoryDepot
from emforge.device.instrument import Instrument

P = testing.FAKE_PROFILE


def fake_factory(**kw):
    return lambda workdir, profile: testing.FakeSimulator(workdir=str(workdir), profile=profile, **kw)


def some_bits(seed=0):
    b = np.random.default_rng(seed).random(P.shape) > 0.5
    b[P.fixed_on] = True
    return b


def make_inst(root, depot=None, tag="216", **kw):
    depot = depot if depot is not None else MemoryDepot()
    testing.make_fake_root(root, depot=depot)
    kw.setdefault("sim_factory", fake_factory())
    kw.setdefault("work_root", root / "work")
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("worker_ver", "emforge=test")
    return Instrument(root, tag, depot=depot, **kw)


@pytest.fixture
def inst(root):
    i = make_inst(root)
    yield i
    i.stop()
