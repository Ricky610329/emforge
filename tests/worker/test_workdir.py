# -*- coding: utf-8 -*-
"""tests/worker/test_workdir.py — `emforge/worker/workdir.py`：本機工作目錄生命週期。

回歸 I-1（2026-07-15）：78 個工作暫存吃光系統碟 → 0x80070223 連環例外。兩道：跑完即刪、啟動整清。
"""
from pathlib import Path

from emforge.worker.workdir import WorkDir, default_work_root


def test_make_remove_idempotent(tmp_path):
    w = WorkDir(tmp_path / "work")
    p = w.make("s1")
    assert p == tmp_path / "work" / "s1" and p.is_dir()
    assert w.make("s1") == p
    (p / "HFSS" / "project").mkdir(parents=True)
    w.remove("s1")
    w.remove("s1")
    assert not p.exists()


def test_sweep_all_removes_only_under_work_root(tmp_path):
    w = WorkDir(tmp_path / "work")
    w.make("s1")
    w.make("s2")
    (tmp_path / "work" / "keep.txt").write_text("x", encoding="utf-8")
    (tmp_path / "other").mkdir()
    removed = w.sweep_all()
    assert sorted(p.name for p in removed) == ["s1", "s2"]
    assert (tmp_path / "work" / "keep.txt").exists() and (tmp_path / "other").exists()
    assert WorkDir(tmp_path / "missing").sweep_all() == []


def test_default_work_root_uses_env(monkeypatch, tmp_path):
    monkeypatch.setenv("EMFORGE_WORK", str(tmp_path / "w"))
    assert default_work_root() == tmp_path / "w"
    monkeypatch.delenv("EMFORGE_WORK")
    d = default_work_root()
    assert isinstance(d, Path) and d.parts[-2:] == ("emforge", "work")
