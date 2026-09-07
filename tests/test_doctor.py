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


def test_probe_root_is_concurrency_safe_and_ignores_leftover_probes(root):
    """檢查 #8（2026-09-07）：探針檔只綁 pid → 同行程並發（worker 主執行緒／心跳刷說明檔／MCP thread pool）互撞成假的
    「root 不可寫」；殘留（Ctrl-C 落在 claim 與 release 之間）永久擋住同 pid 的 open()。探針名加隨機成分、finally 釋放；舊式殘留不影響。"""
    import os
    import threading
    testing.make_fake_root(root)
    leftover = root / f".doctor_probe_{os.getpid()}"
    leftover.write_text("{}", encoding="utf-8")                                     # 舊式殘留
    results, go = [], threading.Barrier(8)

    def probe():
        go.wait()
        results.append(doctor.probe_root(root))

    ts = [threading.Thread(target=probe) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(results) == 8 and all(ok for ok, _ in results), results
    assert [p.name for p in root.iterdir() if p.name.startswith(".doctor_probe_")] == [leftover.name], "自己的探針不留"
