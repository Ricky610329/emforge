# -*- coding: utf-8 -*-
"""tests/adapters/test_antenna_bound.py — 需要舊 repo（`EMFORGE_ANTENNA_REPO` 或已可 import antenna）才跑。

vendored numpy 尺 vs `antenna.losses` 的 parity：500 筆隨機響應 m1..m6 **逐位元**相等；
凍結進 emforge 的 targets 與舊 YAML 相等（這是唯一允許讀舊 YAML 的地方，且只在測試裡）。
"""
import numpy as np
import pytest

from emforge.adapters.antenna import _bind, measure

losses = None
try:
    losses = _bind.load_losses()
except _bind.AntennaUnavailable:
    pass

pytestmark = pytest.mark.skipif(losses is None, reason="antenna 不可 import（設 EMFORGE_ANTENNA_REPO 才跑）")


def test_measure_parity_500_random_responses_bitwise():
    rng = np.random.default_rng(0)
    for _ in range(500):
        r = rng.uniform(-40, 10, (3, 17)).astype(np.float32)
        wm_old, per_old = losses.worst_margin_dual(r, list(measure.DUAL_LABELS), _plain(measure.DUAL_R1_TARGETS))
        wm_new, per_new = measure.worst_margin_dual(r, measure.DUAL_LABELS, measure.DUAL_R1_TARGETS)
        assert wm_new == wm_old
        for k in ("m1", "m2", "m3", "m4", "m5", "m6", "S11", "S21", "S22"):
            assert per_new[k] == per_old[k], k
        assert measure.dual_energy_max(r) == pytest.approx(losses.dual_energy_max(r), rel=1e-6)
        s = rng.uniform(-40, 10, (2, 17)).astype(np.float32)
        wm_o, per_o = losses.worst_margin(s, list(measure.SINGLE_LABELS), _plain(measure.SINGLE_BASE_TARGETS))
        wm_n, per_n = measure.worst_margin(s, measure.SINGLE_LABELS, measure.SINGLE_BASE_TARGETS)
        assert wm_n == wm_o and per_n == per_o


def test_frozen_targets_equal_old_yaml():
    import yaml
    root = _bind.repo_root()
    if root is None:
        pytest.skip("EMFORGE_ANTENNA_REPO 未設，找不到 configs/")
    dual = yaml.safe_load((root / "configs" / "dual_r1_eval.yaml").read_text(encoding="utf-8"))["targets"]
    for lab in measure.DUAL_LABELS:
        for k in ("side", "center", "width"):
            assert _norm(measure.DUAL_R1_TARGETS[lab][k]) == _norm(dual[lab][k]), (lab, k)
    single = yaml.safe_load((root / "configs" / "single_base.yaml").read_text(encoding="utf-8"))["targets"]
    for lab in measure.SINGLE_LABELS:
        for k in ("side", "center", "width", "method"):
            assert _norm(measure.SINGLE_BASE_TARGETS[lab][k]) == _norm(single[lab][k]), (lab, k)


def _norm(v):
    return list(v) if isinstance(v, (list, tuple)) else v


def test_dual_geom_ver_matches_old_module_constant():
    from emforge.adapters.antenna import sim
    sims = _bind.load_sims()
    assert sims.dual_port.GEOM_VER == sim.DualPortSim.geom_ver == "p01"


def _plain(t):
    return {k: dict(v) for k, v in t.items()}
