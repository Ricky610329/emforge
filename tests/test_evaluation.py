"""評估口徑、門檻與獨立樣本計數。"""
from dataclasses import replace
import pytest
from emforge.model import Spec
from emforge import evaluation

def spec(**kwargs):
    return Spec("test_eval", ("x",), "test_measure", ("a", "b"), (0, 1), **kwargs)

def test_default_weighted_and_gates():
    s = spec()
    assert s.score({"a": 2, "b": 4}) == 2
    w = spec(aggregate="wsum", weights=(2, -1), gates=(("c", "<=", 3),))
    assert w.score({"a": 2, "b": 4, "c": 3}) == -1
    assert w.score({"a": 2, "b": 4, "c": 4}) is None
    assert w.score({"a": 2, "b": 4}) is None
    assert s.score({"a": float("inf"), "b": 4}) is None
    for kwargs in ({"aggregate": "unknown"}, {"weights": (1,)},
                   {"gates": (("c", "==", 2),)}, {"offsets": (0, float("nan"))}):
        with pytest.raises(ValueError):
            replace(s, **kwargs)

def test_curve_unique_samples_calibration_ties_and_failed_cost():
    rows = [
        {"id": "a", "kind": "sample", "tick": 1, "score": 1., "note": {"pred": 2}},
        {"id": "a", "kind": "repeat", "tick": 2, "score": 10., "note": {}},
        {"id": "b", "kind": "sample", "tick": 3, "score": None, "note": {}},
        {"id": "c", "kind": "sample", "tick": 4, "score": 3., "note": {"pred": 4}},
        {"id": "a", "kind": "sample", "tick": 5, "score": 1., "note": {"pred": 2}},
    ]
    curve = evaluation.efficiency_curve(rows)
    assert [(r["n"], r["best"]) for r in curve] == [(1, 1), (2, 1), (3, 3)]
    assert evaluation.calibration(rows) == {"n": 2, "mae": 1., "spearman": pytest.approx(1.)}
    rows[3]["note"]["pred"] = 2
    assert evaluation.calibration(rows)["spearman"] is None

def test_metrics_gate_denominator_includes_missing():
    rows = [{"measure": {"a": 2, "b": 4, "c": 2}}, {"measure": {"a": 3, "b": 5}},
            {"measure": {"a": 4, "b": 6, "c": 4}}]
    result = evaluation.metric_summary(rows, spec(gates=(("c", "<=", 3),)))
    assert result["gate_pass_rate"] == pytest.approx(1/3)
    assert result["metrics"]["a"]["mean"] == 3
    assert result["metrics"]["c"]["best"] == 2
