# -*- coding: utf-8 -*-
"""tests/depot/test_file.py — `FileDepot` 專屬：key→路徑、bytes 與舊 `fs` 逐 byte 相同（零資料遷移的證明）、Windows 退避、證據檔。"""
import os
import time

import pytest

from emforge import fs
from emforge.depot import FileDepot, open_depot
from emforge.depot import file as fdep


def test_key_maps_to_path_under_cjk_apostrophe_root(root):
    d = FileDepot(root)
    assert d.path("a/b.json") == root / "a" / "b.json"
    assert d.spec.startswith("file://") and "\\" not in d.spec
    assert open_depot(d.spec).path("x") == d.path("x")


def test_put_leaves_no_tmp_file_and_list_hides_in_progress_tmp(root):
    d = FileDepot(root)
    d.put_bytes("a/x.json", b"1")
    (root / "a" / ".x.json.123.abc.tmp").write_bytes(b"half")
    assert d.list("a/") == ["a/x.json"]
    assert [p.name for p in (root / "a").iterdir() if not p.name.startswith(".")] == ["x.json"]


def test_put_json_bytes_identical_to_fs_atomic_write_json(root):
    d = FileDepot(root)
    obj = {"z": 1, "名": [1, 2.5, None], "a": {"b": True}}
    d.put_json("new.json", obj)
    fs.atomic_write_json(root / "old.json", obj)
    assert (root / "new.json").read_bytes() == (root / "old.json").read_bytes()


def test_claim_file_bytes_identical_to_fs_try_claim(root):
    d = FileDepot(root)
    payload = {"owner": "216", "at": "2026-09-05T10:00:00", "prior_fail": ["37"]}
    d.claim("new.claim", payload)
    fs.try_claim(root / "old.claim", payload)
    assert (root / "new.claim").read_bytes() == (root / "old.claim").read_bytes()


def test_put_retries_on_permission_error_then_fsbusy(root, monkeypatch):
    d = FileDepot(root)
    calls = {"n": 0}
    real = os.replace

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError("sharing violation")
        return real(src, dst)

    monkeypatch.setattr(fs.os, "replace", flaky)
    monkeypatch.setattr(fs.time, "sleep", lambda s: None)
    d.put_bytes("y", b"1")
    assert calls["n"] == 3 and d.get_bytes("y") == b"1"
    monkeypatch.setattr(fs.os, "replace", lambda s, t: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(fs.FsBusy):
        d.put_bytes("y", b"2")
    assert d.get_bytes("y") == b"1"


def test_delete_retries_on_permission_error(root, monkeypatch):
    """review-6：別的行程開著 claim 檔時 unlink 拋 PermissionError → 退避重試。"""
    d = FileDepot(root)
    d.put_bytes("c", b"1")
    calls = {"n": 0}
    real = os.unlink

    def flaky(p):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError("busy")
        return real(p)

    monkeypatch.setattr(fs.os, "unlink", flaky)
    monkeypatch.setattr(fs.time, "sleep", lambda s: None)
    assert d.delete("c") is True and calls["n"] == 3 and not d.exists("c")


def test_owner_none_on_empty_or_half_written_claim(root):
    """I-2：O_EXCL 建檔與寫 JSON 之間死掉 → 空／半截 claim → owner None（上層當壞 claim 清）。"""
    d = FileDepot(root)
    (root / "e.claim").write_text("", encoding="utf-8")
    (root / "h.claim").write_text('{"owner": ', encoding="utf-8")
    assert d.owner("e.claim") is None and d.owner("h.claim") is None
    assert d.exists("e.claim"), "檔還在，只是內容壞——由 break_if_stale 清"


def test_break_if_stale_leaves_broken_evidence_file(root):
    d = FileDepot(root)
    d.claim("q/jobs.lock", {"owner": "dead"})
    d.set_modified_at("q/jobs.lock", time.time() - 1000)
    assert d.break_if_stale("q/jobs.lock", 60) is True
    broken = [p.name for p in (root / "q").iterdir() if ".broken." in p.name]
    assert len(broken) == 1 and broken[0].startswith("jobs.lock.broken.")
    assert d.list("q/") == [], "證據檔是後端內部、不是 key"


def test_break_if_stale_restores_fresh_lease_it_moved_by_mistake(root, monkeypatch):
    """檢查→replace 的窗：A 先破鎖並重新認領，B 的 replace 搬走的是 A 的新鎖 → B 要放回去、回 False。"""
    d = FileDepot(root)
    d.claim("q/l", {"owner": "dead"})
    d.set_modified_at("q/l", time.time() - 1000)
    real = os.replace

    def race(src, dst):
        real(src, str(dst) + ".A")                  # A 先破掉舊鎖
        d.claim("q/l", {"owner": "A"})            # A 重新認領
        return real(src, dst)                     # B 這時才 replace——搬到的是 A 的新鎖

    monkeypatch.setattr(fdep.os, "replace", race)
    assert d.break_if_stale("q/l", 60) is False
    assert d.owner("q/l") == {"owner": "A"}
    assert sum(".broken." in p.name for p in (root / "q").iterdir()) == 1, "只有 A 留下的證據檔"


def test_read_log_bad_line_raises_fscorrupt(root):
    d = FileDepot(root)
    d.append("l.jsonl", {"a": 1})
    with open(root / "l.jsonl", "a", encoding="utf-8") as f:
        f.write('{"broken": \n')
    with pytest.raises(fs.FsCorrupt):
        d.read_log("l.jsonl")


def test_selfcheck_reports_unwritable_root(tmp_path):
    f = tmp_path / "not_a_dir"
    f.write_text("x", encoding="utf-8")
    problems = FileDepot(f / "sub").selfcheck()
    assert problems and any("寫" in p or "write" in p.lower() for p in problems)


def test_selfcheck_reports_clock_skew(root, monkeypatch):
    d = FileDepot(root)
    monkeypatch.setattr(fdep, "_probe_mtime", lambda path: time.time() + 3600)
    problems = d.selfcheck()
    assert problems and any("clock" in p.lower() or "時鐘" in p for p in problems)


def test_set_modified_at_changes_modified_at(root):
    d = FileDepot(root)
    d.put_bytes("x", b"1")
    d.set_modified_at("x", 1_000_000)
    assert d.modified_at("x") == 1_000_000
