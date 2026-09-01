# -*- coding: utf-8 -*-
"""tests/test_profiles.py — `emforge/profiles.py`：profile 註冊表、模擬器載入、GEOM_VER 比對、退役、使用者註冊表。

防什麼：I-7（幾何換代、註冊表沒人改）——比對要在**建構模擬器之前**；I-6（用錯儀器）——雙邊宣告。
"""
import dataclasses

import numpy as np
import pytest

from emforge import model, paths, profiles


def _profile(**over):
    base = dict(name="fake_f1", simulator="tests.test_profiles:_StubSim", geom_ver="g1", kwargs={"k": 1},
                shape=(4, 4), labels=("L1",), n_points=5, fixed_on=np.zeros((4, 4), bool),
                measure="fake_m", spec="fake_v1", timeout_s=5)
    base.update(over)
    return model.Profile(**base)


class _StubSim(model.Simulator):
    geom_ver = "g1"
    labels = ("L1",)
    constructed = []

    def __init__(self, *, workdir, profile):
        super().__init__(workdir=workdir, profile=profile)
        _StubSim.constructed.append((workdir, profile.name))


class _NoGeomSim(_StubSim):
    geom_ver = None


@pytest.fixture(autouse=True)
def _reset_stub():
    _StubSim.constructed.clear()


def test_register_profile_append_only():
    p = _profile()
    profiles.register_profile(p)
    profiles.register_profile(_profile())  # 同內容＝no-op
    assert profiles.get_profile("fake_f1").kwargs == {"k": 1}
    with pytest.raises(profiles.RegistryConflict):
        profiles.register_profile(_profile(kwargs={"k": 2}))
    with pytest.raises(profiles.RegistryConflict):
        profiles.register_profile(dataclasses.replace(p, timeout_s=99))
    assert [q.name for q in profiles.all_profiles()] == ["fake_f1"]
    with pytest.raises(profiles.UnknownProfile):
        profiles.get_profile("nope")


def test_make_simulator_refuses_on_geom_ver_mismatch_before_constructing(root):
    """回歸 I-7（2026-08-13）：類別名沒變、幾何底板換代。註冊表說 g2、模擬器說 g1 → 拒開，且建構子一次都沒跑。"""
    p = _profile(geom_ver="g2")
    with pytest.raises(profiles.GeomVerMismatch):
        profiles.make_simulator(p, root / "w")
    assert _StubSim.constructed == []
    sim = profiles.make_simulator(_profile(), root / "w")
    assert isinstance(sim, _StubSim) and _StubSim.constructed == [(str(root / "w"), "fake_f1")]


def test_geom_ver_check_skipped_when_simulator_declares_none(root):
    """single 那類沒有版號常數的模擬器：單邊宣告（profile 說了算），不擋。"""
    p = _profile(simulator="tests.test_profiles:_NoGeomSim", geom_ver="whatever")
    sim = profiles.make_simulator(p, root / "w")
    assert isinstance(sim, _NoGeomSim)


def test_make_simulator_refuses_labels_mismatch(root):
    p = _profile(labels=("L1", "L2"))
    with pytest.raises(profiles.LabelsMismatch):
        profiles.make_simulator(p, root / "w")
    assert _StubSim.constructed == []


def test_load_simulator_class_malformed_or_missing_raises():
    with pytest.raises(ValueError):
        profiles.load_simulator_class(_profile(simulator="no_colon_here"))
    with pytest.raises(ImportError):
        profiles.load_simulator_class(_profile(simulator="emforge.nonexistent_mod:X"))
    with pytest.raises(AttributeError):
        profiles.load_simulator_class(_profile(simulator="tests.test_profiles:_Nope"))


def test_is_retired_by_flag_or_marker(root):
    p = _profile()
    profiles.register_profile(p)
    assert profiles.is_retired(root, p) is False
    paths.retired_marker(root, p.name).parent.mkdir(parents=True)
    paths.retired_marker(root, p.name).write_text('{"by": "ricky"}', encoding="utf-8")
    assert profiles.is_retired(root, p) is True
    assert profiles.is_retired(root, _profile(name="fake_f2", retired=True)) is True


def test_load_user_registry_runs_root_registry_py_and_is_optional(root):
    assert profiles.load_user_registry(root) is False
    paths.registry_py(root).write_text(
        "from emforge import profiles, model\n"
        "import numpy as np\n"
        "profiles.register_profile(model.Profile(name='user_p', simulator='tests.test_profiles:_StubSim', geom_ver='g1',\n"
        "    kwargs={}, shape=(4, 4), labels=('L1',), n_points=5, fixed_on=np.zeros((4, 4), bool),\n"
        "    measure='fake_m', spec='fake_v1', timeout_s=5))\n",
        encoding="utf-8")
    assert profiles.load_user_registry(root) is True
    assert profiles.get_profile("user_p").geom_ver == "g1"
    assert profiles.load_user_registry(root) is True, "重跑＝重註冊同內容＝no-op"
