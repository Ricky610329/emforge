# -*- coding: utf-8 -*-
"""tests/test_specs.py — `emforge/specs.py`：量測尺（measure）與評估器（spec）註冊表，append-only。

防什麼：同名不同內容的靜默覆蓋（等於偷換儀器）；策略／runtime 各自算分（D1：runtime 統一算分）。
"""
import numpy as np
import pytest

from emforge import model, specs

TARGETS = {"L1": {"center": -10.0}}


def _m(response, labels, targets):
    return {"m1": np.float32(targets["L1"]["center"] - response[0].max()), "m2": 3}


def _spec(**over):
    base = dict(name="fake_v1", labels=("L1",), measure="fake_m", axes=("m1", "m2"), offsets=(0.0, 1.0))
    base.update(over)
    return model.Spec(**base)


def test_register_measure_same_twice_idempotent_changed_rejected():
    specs.register_measure("fake_m", _m, labels=("L1",), targets=TARGETS)
    specs.register_measure("fake_m", _m, labels=("L1",), targets=TARGETS)  # 同內容重註冊＝no-op
    assert specs.get_measure("fake_m").targets == TARGETS
    with pytest.raises(specs.RegistryConflict):
        specs.register_measure("fake_m", _m, labels=("L1",), targets={"L1": {"center": -12.0}})
    with pytest.raises(specs.RegistryConflict):
        specs.register_measure("fake_m", lambda r, l, t: {}, labels=("L1",), targets=TARGETS)
    with pytest.raises(ValueError):
        specs.register_measure("Bad-Name", _m, labels=("L1",), targets=TARGETS)


def test_register_spec_same_twice_idempotent_changed_rejected():
    specs.register_spec(_spec())
    specs.register_spec(_spec())
    assert specs.get_spec("fake_v1").offsets == (0.0, 1.0)
    with pytest.raises(specs.RegistryConflict):
        specs.register_spec(_spec(offsets=(2.0, 1.0)))


def test_measure_dispatches_by_name_binds_targets_coerces_floats():
    """targets 由註冊表綁定（凍結），呼叫端不能塞別的；回傳值一律轉成 python float。"""
    seen = {}

    def m(response, labels, targets):
        seen["targets"], seen["labels"] = targets, tuple(labels)
        return {"m1": np.float32(-1.5), "m2": 2, "m3": np.int64(7)}

    specs.register_measure("fake_m", m, labels=("L1",), targets=TARGETS)
    out = specs.measure("fake_m", np.zeros((1, 17), np.float32), ("L1",))
    assert out == {"m1": -1.5, "m2": 2.0, "m3": 7.0}
    assert all(type(v) is float for v in out.values())
    assert seen["targets"] == TARGETS and seen["labels"] == ("L1",)


def test_measure_rejects_label_mismatch_before_calling_fn():
    """I-6 型：儀器給的 labels 與尺要的不同，跑之前就擋。"""
    called = []
    specs.register_measure("fake_m", lambda r, l, t: called.append(1) or {}, labels=("L1", "L2"), targets={})
    with pytest.raises(specs.MeasureLabelsMismatch):
        specs.measure("fake_m", np.zeros((1, 17), np.float32), ("L1",))
    assert not called


def test_unknown_measure_or_spec_raises():
    with pytest.raises(specs.UnknownMeasure):
        specs.get_measure("nope")
    with pytest.raises(specs.UnknownSpec):
        specs.get_spec("nope")
    with pytest.raises(specs.UnknownMeasure):
        specs.measure("nope", np.zeros((1, 17)), ("L1",))


def test_score_uses_registered_spec():
    specs.register_spec(_spec())
    assert specs.score("fake_v1", {"m1": -3.0, "m2": -1.0}) == pytest.approx(-3.0)
    assert specs.score("fake_v1", {"m1": -3.0}) is None
    assert specs.spec_names() == ["fake_v1"] and specs.measure_names() == []
