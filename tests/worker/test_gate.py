# -*- coding: utf-8 -*-
"""tests/worker/test_gate.py — `emforge/worker/gate.py`：開模擬器前守門。

回歸 I-6（2026-08-31）：漏帶設定量錯元件、結果欄位齊全不報錯；I-7：幾何換代註冊表沒改。
守門順序固定、擋在任何昂貴操作之前、**不建構不 open**。
"""
import dataclasses

from emforge import model, paths, profiles, testing
from emforge.worker.gate import gate
from tests.worker.conftest import make_job

P = testing.FAKE_PROFILE


class _NoGeomSim(testing.FakeSimulator):
    geom_ver = None
    opened = []

    def open(self):
        _NoGeomSim.opened.append(1)


def test_gate_order_profile_exists_hash_geom_labels_and_stops_at_first_failure(root):
    testing.make_fake_root(root)
    v = gate(make_job("s", profile_hash="0" * 12), root)
    assert not v.ok and v.reason.startswith("profile_hash_mismatch") and v.profile is None
    v = gate(dataclasses.replace(make_job("s"), sim_profile="nope"), root)
    assert not v.ok and v.reason.startswith("unknown_profile")
    bad_geom = dataclasses.replace(P, name="fake_g2", geom_ver="fake2")
    profiles.register_profile(bad_geom)
    v = gate(model.Job(store="s", sim_profile="fake_g2", profile_hash=bad_geom.profile_hash, prio=5, n=1), root)
    assert not v.ok and v.reason.startswith("geom_ver_mismatch")
    bad_labels = dataclasses.replace(P, name="fake_l3", labels=("L1", "L2", "L3"))
    profiles.register_profile(bad_labels)
    v = gate(model.Job(store="s", sim_profile="fake_l3", profile_hash=bad_labels.profile_hash, prio=5, n=1), root)
    assert not v.ok and v.reason.startswith("labels_mismatch")
    v = gate(make_job("s"), root)
    assert v.ok and v.reason is None and v.profile.name == P.name and v.sim_cls is testing.FakeSimulator


def test_gate_rejects_retired_profile(root):
    testing.make_fake_root(root)
    (root / paths.retired_marker(P.name)).parent.mkdir(parents=True, exist_ok=True)
    (root / paths.retired_marker(P.name)).write_text("{}", encoding="utf-8")
    v = gate(make_job("s"), root)
    assert not v.ok and v.reason.startswith("profile_retired")


def test_gate_passes_when_sim_geom_ver_none_single_sided(root):
    testing.make_fake_root(root)
    p = dataclasses.replace(P, name="fake_single", geom_ver="anything", simulator="tests.worker.test_gate:_NoGeomSim")
    profiles.register_profile(p)
    v = gate(model.Job(store="s", sim_profile="fake_single", profile_hash=p.profile_hash, prio=5, n=1), root)
    assert v.ok and v.sim_cls is _NoGeomSim


def test_gate_never_constructs_or_opens(root):
    testing.make_fake_root(root)
    _NoGeomSim.opened.clear()
    p = dataclasses.replace(P, name="fake_single", geom_ver="x", simulator="tests.worker.test_gate:_NoGeomSim")
    profiles.register_profile(p)
    gate(model.Job(store="s", sim_profile="fake_single", profile_hash=p.profile_hash, prio=5, n=1), root)
    assert _NoGeomSim.opened == []
