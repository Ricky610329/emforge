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


def test_frozen_run_and_submission_survive_registry_spec_change(root, monkeypatch):
    from emforge import specs, testing
    from emforge.client import Client
    from emforge.platform.service import Platform
    from emforge.runtime import collect
    from tests.test_client import setup, patterns
    rt = setup(root)
    service = Platform(rt.depot)
    service.call("node_heartbeat", node="node_a", session="session", environments=["ant"], max_runs=1)
    version = service.call("algorithm_register", name="anneal", files={"main.py": "pass"}, entrypoint="main.py")
    service.call("run_start", name="anneal", version=version, run_id="frozen", node="node_a",
                 environment="ant", profile="fake_f1")
    client = Client(rt.depot, "fake_f1", "anneal", run_id="frozen")
    sid = client.submit(patterns(1))
    rt.tick()
    testing.run_all_jobs(root)
    collect.collect(rt)
    original = client.results(sid)[0].score
    old = specs.get_spec(rt.profile.spec)
    monkeypatch.setitem(specs._SPECS, old.name, replace(old, offsets=tuple(x+100 for x in old.offsets)))
    assert client.results(sid)[0].score == original
    assert service.call("evaluate", profile="fake_f1", run_id="frozen")["result"][-1]["best"] == original
    another = client.submit(patterns(1))
    rt.tick()
    assert client.results(another)[0].score == original
