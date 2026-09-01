# -*- coding: utf-8 -*-
"""tests/adapters/test_antenna_pure.py — 天線轉接層，**不需要**舊 repo／torch／HFSS：用 stub 當舊模擬器。

防什麼：I-6（labels／形狀對不上、跑完才發現）、I-7（幾何版）、尺被偷換（凍結 targets）、torch 漏進核心 import 鏈。
"""
import subprocess
import sys

import numpy as np
import pytest

from emforge import model, profiles, specs
from emforge.adapters.antenna import _bind, measure, sim
from emforge.adapters.antenna import profiles as aprof

N = 17


class _StubOld:
    """假舊模擬器：記錄生命週期、回傳亂序 dict（label → 17 點）。"""
    GEOM_VER = "p01"
    out = None
    raise_in_call = False

    def __init__(self, record_path, **kw):
        self.record_path, self.kw, self.calls = record_path, kw, []
        self.last_radiation = None

    def open(self):
        self.calls.append("open")

    def start(self, num):
        self.calls.append(("start", num))

    def __call__(self, t):
        self.calls.append("call")
        if _StubOld.raise_in_call:
            raise RuntimeError("COM 炸了")
        return dict(_StubOld.out)

    def end(self, save_project=True):
        self.calls.append(("end", save_project))
        return 123

    def quit(self):
        self.calls.append("quit")

    def kill(self):
        self.calls.append("kill")


class _StubNoGeom(_StubOld):
    GEOM_VER = None


@pytest.fixture(autouse=True)
def _reset_stub():
    _StubOld.out = {"S22": np.full(N, -22.0, np.float32), "S11": np.full(N, -11.0, np.float32),
                    "S21": np.full(N, -2.0, np.float32)}
    _StubOld.raise_in_call = False
    aprof.register_all()


def _dual():
    return profiles.get_profile("dual_p01_db075")


def _single():
    return profiles.get_profile("single_db100")


# ── import 鏈 ───────────────────────────────────────────────────────────────
def test_importing_adapter_does_not_import_antenna_or_torch():
    code = ("import sys, emforge.adapters.antenna.sim, emforge.adapters.antenna.measure, emforge.adapters.antenna.profiles;"
            "print('torch' in sys.modules, 'antenna' in sys.modules)")
    out = subprocess.run([sys.executable, "-X", "utf8", "-c", code], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False False"


# ── sim 包裝 ────────────────────────────────────────────────────────────────
def test_stack_order_follows_profile_labels_f32_shape(root):
    s = sim.DualPortSim(workdir=str(root), profile=_dual(), old_cls=_StubOld)
    s.open()
    r = s.simulate(np.zeros((25, 25), bool))
    assert r.response.shape == (3, N) and r.response.dtype == np.float32
    assert r.response[0, 0] == -11.0 and r.response[1, 0] == -2.0 and r.response[2, 0] == -22.0, "依 profile.labels 序"
    assert r.time_s == 123.0 and r.extra == {}


def test_missing_label_or_wrong_n_points_raises(root):
    s = sim.DualPortSim(workdir=str(root), profile=_dual(), old_cls=_StubOld)
    del _StubOld.out["S21"]
    with pytest.raises(sim.AdapterError, match="S21"):
        s.simulate(np.zeros((25, 25), bool))
    _StubOld.out["S21"] = np.zeros(16, np.float32)
    with pytest.raises(sim.AdapterError, match="17"):
        s.simulate(np.zeros((25, 25), bool))


def test_forbidden_kwargs_rejected(root):
    import dataclasses
    for bad in ("record_path", "HFSS_sab_path"):
        p = dataclasses.replace(_dual(), name="fake_bad", kwargs={**_dual().kwargs, bad: "x"})
        with pytest.raises(sim.ProfileInconsistent, match=bad):
            sim.DualPortSim(workdir=str(root), profile=p, old_cls=_StubOld)


def test_labels_shape_npoints_mismatch_rejected_before_construct(root):
    import dataclasses
    constructed = []

    class _Spy(_StubOld):
        def __init__(self, *a, **k):
            constructed.append(1)
            super().__init__(*a, **k)

    for over in (dict(labels=("S11", "S21")), dict(shape=(50, 50), fixed_on=np.zeros((50, 50), bool)), dict(n_points=16)):
        p = dataclasses.replace(_dual(), name="fake_bad", **over)
        with pytest.raises(sim.ProfileInconsistent):
            sim.DualPortSim(workdir=str(root), profile=p, old_cls=_Spy)
    assert constructed == [], "不一致在建構舊模擬器之前就擋"


def test_simulate_calls_start_call_end_in_order_and_ends_on_exception(root):
    s = sim.DualPortSim(workdir=str(root), profile=_dual(), old_cls=_StubOld)
    s.open()
    s.simulate(np.zeros((25, 25), bool))
    s.simulate(np.zeros((25, 25), bool))
    old = s._sim
    assert old.calls == ["open", ("start", 1), "call", ("end", True), ("start", 2), "call", ("end", True)]
    _StubOld.raise_in_call = True
    with pytest.raises(RuntimeError, match="COM"):
        s.simulate(np.zeros((25, 25), bool))
    assert old.calls[-2:] == ["call", ("end", False)], "炸了也要 end（不存專案）把 HFSS 專案關掉"
    s.kill()
    s.close()
    assert old.calls[-2:] == ["kill", "quit"]
    assert old.kw == _dual().kwargs and old.record_path == str(root), "kwargs 原名直傳、workdir→record_path"


def test_single_last_radiation_goes_to_extra_dual_has_none(root):
    _StubOld.out = {"Gain": np.full(N, 5.0, np.float32), "S11": np.full(N, -12.0, np.float32)}
    s = sim.SinglePortRadSim(workdir=str(root), profile=_single(), old_cls=_StubNoGeom)
    s.open()
    s._sim.last_radiation = {"theta": np.arange(3.0), "phi0": np.ones(3), "phi90": np.zeros(3)}
    r = s.simulate(np.zeros((25, 25), bool))
    assert r.response.shape == (2, N) and r.response[1, 0] == 5.0
    assert r.extra == {"radiation": {"theta": [0.0, 1.0, 2.0], "phi0": [1.0, 1.0, 1.0], "phi90": [0.0, 0.0, 0.0]}}
    s._sim.last_radiation = {"error": "boom"}
    assert s.simulate(np.zeros((25, 25), bool)).extra == {}


def test_dual_geom_ver_missing_or_mismatch_raises_single_declared_only(root):
    class _Old(_StubOld):
        GEOM_VER = "p00"

    with pytest.raises(profiles.GeomVerMismatch, match="p00"):
        sim.DualPortSim(workdir=str(root), profile=_dual(), old_cls=_Old)
    with pytest.raises(profiles.GeomVerMismatch, match="GEOM_VER"):
        sim.DualPortSim(workdir=str(root), profile=_dual(), old_cls=_StubNoGeom)
    assert sim.DualPortSim.geom_ver == "p01" and sim.SinglePortRadSim.geom_ver == "s00"
    sim.SinglePortRadSim(workdir=str(root), profile=_single(), old_cls=_StubNoGeom)   # single 單邊：舊模組無版號也行


def test_bind_missing_gives_message_with_env_name(monkeypatch, tmp_path):
    monkeypatch.setenv(_bind.ENV, str(tmp_path / "not_a_repo"))
    monkeypatch.setitem(sys.modules, "antenna", None)
    monkeypatch.setitem(sys.modules, "antenna.losses", None)
    assert _bind.repo_root() is None
    with pytest.raises(_bind.AntennaUnavailable, match=_bind.ENV):
        _bind.load_losses()
    assert _bind.antenna_sha() == "unknown"


# ── 凍結的尺 ────────────────────────────────────────────────────────────────
def _dual_response():
    r = np.zeros((3, N), np.float32)
    r[0] = -20.0
    r[0, 0] = -1.0                      # 帶外一點反射很強（不影響 m1，拉高 m5 的 min？不：min 取最小值）
    r[1] = -30.0
    r[1, 3:14] = -1.0                   # S21 通帶 idx 3-13
    r[2] = -15.0
    return r


def test_measure_dual_r1_known_values_and_direction_check():
    m = measure.measure_dual_r1(_dual_response(), measure.DUAL_LABELS, measure.DUAL_R1_TARGETS)
    assert m["m1"] == 8.0 and m["m2"] == 3.0 and m["m3"] == 2.0 and m["m4"] == 10.0
    assert m["m5"] == -18.75 and m["m6"] == -13.75
    assert m["energy_max"] == pytest.approx(0.826, abs=1e-3)
    wm, per = measure.worst_margin_dual(_dual_response(), measure.DUAL_LABELS, measure.DUAL_R1_TARGETS)
    assert wm == 2.0 and per["S21"] == 2.0 and per["S11"] == 8.0 and per["S22"] == 3.0
    flipped = {**dict(measure.DUAL_R1_TARGETS), "S11": {"side": -12.0, "center": -1.25, "width": [4, 2, 5, 2, 4]}}
    with pytest.raises(ValueError, match="center < side"):
        measure.worst_margin_dual(_dual_response(), measure.DUAL_LABELS, flipped)
    with pytest.raises(ValueError, match="S21"):
        measure.worst_margin_dual(_dual_response(), ("S11", "Gain", "S22"), measure.DUAL_R1_TARGETS)


def test_measure_single_base_known_values():
    r = np.full((2, N), -3.0, np.float32)
    r[0, 5:12] = -15.0
    r[1] = -20.0
    r[1, 5:12] = 6.0
    m = measure.measure_single_base(r, measure.SINGLE_LABELS, measure.SINGLE_BASE_TARGETS)
    assert m == {"S11": 5.0, "Gain": 2.0}
    wm, per = measure.worst_margin(r, measure.SINGLE_LABELS, measure.SINGLE_BASE_TARGETS)
    assert wm == 2.0 and per == {"S11": 5.0, "Gain": 2.0}
    with pytest.raises(ValueError, match="width"):
        measure.worst_margin(np.zeros((2, 10), np.float32), measure.SINGLE_LABELS, measure.SINGLE_BASE_TARGETS)


def test_targets_frozen():
    with pytest.raises(TypeError):
        measure.DUAL_R1_TARGETS["S11"]["center"] = 0.0
    with pytest.raises(TypeError):
        measure.DUAL_R1_TARGETS["S99"] = {}
    tweaked = {k: dict(v) for k, v in measure.DUAL_R1_TARGETS.items()}
    tweaked["S11"]["center"] = -10.0
    with pytest.raises(measure.MeasureFrozen):
        measure.measure_dual_r1(_dual_response(), measure.DUAL_LABELS, tweaked)
    assert measure.DUAL_R1_TARGETS["S11"]["center"] == -12.0 and measure.DUAL_R1_TARGETS["S21"]["side"] == -20.0
    assert measure.SINGLE_BASE_TARGETS["Gain"]["method"] == "high"


def test_dual_energy_max_known_value():
    r = np.full((3, N), -10.0, np.float32)
    assert measure.dual_energy_max(r) == pytest.approx(0.2, rel=1e-6)


# ── 註冊 ────────────────────────────────────────────────────────────────────
def test_spec_dual_v2_equals_wm_mfg_formula():
    v2 = specs.get_spec("dual_v2")
    assert v2.axes == ("m1", "m2", "m3", "m4") and v2.offsets == (2.0, 2.0, 0.0, 5.0) and v2.measure == "antenna_dual_r1"
    m = {"m1": -4.0, "m2": -3.0, "m3": -1.0, "m4": -8.0}
    assert v2.score(m) == min(-4 + 2, -3 + 2, -1, -8 + 5) == -3.0
    assert specs.get_spec("dual_v1").offsets == (0.0, 0.0, 0.0, 0.0)
    assert specs.get_spec("single_v1").axes == ("S11", "Gain")


def test_profile_fixed_on_masks():
    d, s = _dual(), _single()
    assert d.fixed_on.sum() == 50 and d.fixed_on[0:5, 10:15].all() and d.fixed_on[20:25, 10:15].all()
    assert not d.fixed_on[5:20].any() and not d.fixed_on[:, :10].any()
    assert s.fixed_on.sum() == 25 and s.fixed_on[20:25, 10:15].all() and s.fixed_on[24, 12]
    assert d.labels == ("S11", "S21", "S22") and s.labels == ("S11", "Gain") and d.n_points == s.n_points == 17


def test_profile_kwargs_explicit_and_hash_sensitive_to_dbw():
    d = _dual()
    assert d.kwargs == {"pixel_count": 25, "sweep_type": "Fast", "diag_bridge_w": 0.075,
                        "max_delta_s": 0.02, "max_passes": 6, "min_passes": 5, "min_converged": 5}
    assert d.profile_hash == _dual().profile_hash
    assert d.profile_hash != profiles.get_profile("dual_p01").profile_hash
    assert _single().kwargs["sweep_type"] == "Interpolating" and _single().kwargs["diag_bridge_w"] == 0.1
    assert d.spec == "dual_v2" and d.measure == "antenna_dual_r1" and d.timeout_s == 900
    for name in ("dual_p00", "dual_p01", "single_p00", "harvest_dual", "harvest_single"):
        assert profiles.get_profile(name).retired is True, name
    assert not d.retired and not _single().retired


def test_register_all_idempotent_and_measure_dispatch():
    aprof.register_all()
    aprof.register_all()
    m = specs.measure("antenna_dual_r1", _dual_response(), measure.DUAL_LABELS)
    assert m["m1"] == 8.0 and specs.score("dual_v2", m) == pytest.approx(min(10, 5, 2, 15))
    assert specs.measure("antenna_single_base", np.full((2, N), -3.0, np.float32), measure.SINGLE_LABELS)["S11"] == -7.0
    assert model.record_id(np.zeros((25, 25), bool), "dual_p01_db075") != model.record_id(np.zeros((25, 25), bool), "dual_p01")
