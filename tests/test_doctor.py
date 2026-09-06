# -*- coding: utf-8 -*-
"""tests/test_doctor.py — `emforge/doctor.py`：`health()` 純函式（儀器層前置檢查重用）＋ `run()` 只是印它。"""
from emforge import doctor, testing


def test_health_returns_machine_facts_without_printing(root, monkeypatch, capsys):
    testing.make_fake_root(root)
    monkeypatch.setattr(doctor, "ansysedt_running", lambda: False)
    h = doctor.health(root, depot=root)
    assert capsys.readouterr().out == ""
    assert h["root_ok"] is True and isinstance(h["free_gb"], float) and h["ansysedt_running"] is False
    assert h["depot_problems"] == [] and h["emforge_ver"].startswith("emforge=") and h["machine_tag"]
    assert h["blocking"] == []


def test_health_lists_blocking_reasons_low_disk_and_ansysedt(root, monkeypatch):
    testing.make_fake_root(root)
    monkeypatch.setattr(doctor, "ansysedt_running", lambda: True)
    monkeypatch.setattr(doctor, "_free_gb", lambda p: 1.0)
    h = doctor.health(root, depot=root)
    assert any("ansysedt" in b for b in h["blocking"]) and any("GB" in b for b in h["blocking"])
    assert doctor.run(root, depot=root, out=lambda s: None) == 4
