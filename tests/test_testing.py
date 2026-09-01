# -*- coding: utf-8 -*-
"""tests/test_testing.py — `emforge/testing.py`：FakeSimulator 與假 profile／spec／measure。

假件本身也要有測試：它是 worker／runtime 全部測試的地基，行為不對整套測試都在測空氣。
"""
import threading

import numpy as np
import pytest

from emforge import model, profiles, specs, testing


def _bits(seed):
    return np.random.default_rng(seed).random(testing.FAKE_PROFILE.shape) > 0.5


def test_fake_simulator_deterministic_both_ways(root):
    sim = testing.FakeSimulator(workdir=str(root), profile=testing.FAKE_PROFILE)
    sim.open()
    a, b = sim.simulate(_bits(1)), sim.simulate(_bits(1))
    c = sim.simulate(_bits(2))
    assert np.array_equal(a.response, b.response), "同 bits 同響應"
    assert not np.array_equal(a.response, c.response), "不同 bits 不同響應"
    assert a.response.shape == (len(testing.FAKE_PROFILE.labels), testing.FAKE_PROFILE.n_points)
    assert a.response.dtype == np.float32 and a.time_s >= 0 and a.extra == {}
    sim.close()
    assert sim.calls == {"open": 1, "simulate": 3, "kill": 0, "close": 1}


def test_fake_simulator_fail_ids_raise_and_lifecycle_counters(root):
    b = _bits(3)
    rid = model.record_id(b, testing.FAKE_PROFILE.name)
    sim = testing.FakeSimulator(workdir=str(root), profile=testing.FAKE_PROFILE, fail_ids={rid})
    sim.open()
    with pytest.raises(testing.FakeFailure):
        sim.simulate(b)
    sim.simulate(_bits(4))  # 別的 id 照常
    assert sim.calls["simulate"] == 2


def test_fake_simulator_hang_ids_block_until_kill_then_raise(root):
    """看門狗情境：simulate 卡住 → kill() → 卡住的呼叫拋錯（模仿 COM 呼叫被殺後拋例外）。"""
    b = _bits(5)
    rid = model.record_id(b, testing.FAKE_PROFILE.name)
    sim = testing.FakeSimulator(workdir=str(root), profile=testing.FAKE_PROFILE, hang_ids={rid})
    sim.open()
    result = {}

    def run():
        try:
            sim.simulate(b)
        except Exception as e:  # noqa: BLE001
            result["exc"] = e

    t = threading.Thread(target=run)
    t.start()
    t.join(0.3)
    assert t.is_alive(), "還卡著"
    sim.kill()
    t.join(2)
    assert not t.is_alive() and isinstance(result["exc"], testing.FakeKilled)
    assert sim.calls["kill"] == 1


def test_fake_measure_returns_m1_to_m4():
    r = np.full((2, 17), -30.0, np.float32)
    m = testing.fake_measure(r, testing.FAKE_PROFILE.labels, testing.FAKE_TARGETS)
    assert set(m) == {"m1", "m2", "m3", "m4"} and all(isinstance(v, float) for v in m.values())
    assert m["m1"] == pytest.approx(testing.FAKE_TARGETS["L1"]["center"] + 30.0)


def test_register_fakes_idempotent_and_make_simulator_works(root):
    testing.register_fakes()
    testing.register_fakes()
    p = profiles.get_profile("fake_f1")
    assert p is testing.FAKE_PROFILE or p.profile_hash == testing.FAKE_PROFILE.profile_hash
    assert specs.get_spec("fake_v1").measure == "fake_m"
    sim = profiles.make_simulator(p, root / "w")
    sim.open()
    out = sim.simulate(_bits(6))
    m = specs.measure(p.measure, out.response, p.labels)
    assert specs.score(p.spec, m) is not None


def test_fake_profile_shape_and_fixed_on():
    p = testing.FAKE_PROFILE
    assert p.shape == (8, 8) and p.n_points == 17 and p.fixed_on.sum() == 4 and p.fixed_on[:2, :2].all()
    assert p.geom_ver == testing.GEOM_VER == testing.FakeSimulator.geom_ver
