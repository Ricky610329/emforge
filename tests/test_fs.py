# -*- coding: utf-8 -*-
"""tests/test_fs.py — `emforge/fs.py`：檔案系統原語（原子寫入、認領、陳舊判定、jsonl、清掃）。

這是 O-3（協調層假設 O_EXCL 近似原子 + mtime 可信）的落點；鎖的測試在 tests/depot/test_contract.py（`Depot.lock`）。
全部在 tmp_path 跑；SMB 真實行為靠正式機 `emforge doctor --probe-fs` 驗。
"""
import os
import threading
import time

import pytest

from emforge import fs


# ── 原子寫入 ────────────────────────────────────────────────────────────────
def test_atomic_write_json_roundtrips_and_leaves_no_tmp(root):
    p = root / "a" / "b.json"
    fs.atomic_write_json(p, {"名": "值", "n": [1, 2.5, None], "z": True})
    assert fs.read_json(p) == {"名": "值", "n": [1, 2.5, None], "z": True}
    assert [x.name for x in p.parent.iterdir()] == ["b.json"], "不留 .tmp"


def test_atomic_write_keeps_old_file_when_serialization_fails(root):
    p = root / "x.json"
    fs.atomic_write_json(p, {"ok": 1})
    with pytest.raises(TypeError):
        fs.atomic_write_json(p, {"bad": object()})
    assert fs.read_json(p) == {"ok": 1}
    assert [x.name for x in root.iterdir()] == ["x.json"]


def test_atomic_write_concurrent_threads_reader_always_parses(root):
    """多執行緒交錯寫同一檔，讀者永遠讀到完整的舊檔或新檔（tmp→replace 語義）。"""
    p = root / "hot.json"
    fs.atomic_write_json(p, {"i": -1, "t": -1})
    bad, errors, stop = [], [], threading.Event()

    def writer(tid):
        try:
            for i in range(30):
                fs.atomic_write_json(p, {"i": i, "t": tid})
        except Exception as e:  # noqa: BLE001 — 執行緒例外要收進斷言，不能只變 warning
            errors.append(("writer", tid, repr(e)))

    def reader():
        try:
            while not stop.is_set():
                d = fs.read_json(p)
                if set(d) != {"i", "t"}:
                    bad.append(d)
                time.sleep(0.001)  # 讀者是輪詢，不是忙迴圈
        except Exception as e:  # noqa: BLE001
            errors.append(("reader", repr(e)))

    r = threading.Thread(target=reader)
    ws = [threading.Thread(target=writer, args=(t,)) for t in range(3)]
    r.start()
    for w in ws:
        w.start()
    for w in ws:
        w.join()
    stop.set()
    r.join()
    assert not errors
    assert not bad
    assert fs.read_json(p)["i"] == 29


def test_atomic_write_retries_on_permission_error(root, monkeypatch):
    """Windows/SMB：目標被別人短暫開著時 os.replace 會拋 PermissionError → 退避重試，用盡才拋 FsBusy。"""
    p = root / "y.json"
    calls = {"n": 0}
    real = os.replace

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError("sharing violation")
        return real(src, dst)

    monkeypatch.setattr(fs.os, "replace", flaky)
    monkeypatch.setattr(fs.time, "sleep", lambda s: None)
    fs.atomic_write_json(p, {"v": 1})
    assert calls["n"] == 3 and fs.read_json(p) == {"v": 1}

    def always(src, dst):
        raise PermissionError("locked")

    monkeypatch.setattr(fs.os, "replace", always)
    with pytest.raises(fs.FsBusy):
        fs.atomic_write_json(p, {"v": 2})
    assert fs.read_json(p) == {"v": 1}, "失敗不動舊檔"
    assert [x.name for x in root.iterdir()] == ["y.json"], "失敗不留 tmp"


def test_read_json_default_and_corrupt(root):
    assert fs.read_json(root / "nope.json", default=None) is None
    with pytest.raises(FileNotFoundError):
        fs.read_json(root / "nope.json")
    (root / "half.json").write_text('{"a": 1, "b"', encoding="utf-8")
    with pytest.raises(fs.FsCorrupt):
        fs.read_json(root / "half.json", retries=1)


# ── 認領 ────────────────────────────────────────────────────────────────────
def test_try_claim_second_caller_false_and_body_is_json(root):
    c = root / "state" / "s.claim"
    assert fs.try_claim(c, {"machine": "216", "at": "x"}) is True
    assert fs.try_claim(c, {"machine": "218", "at": "y"}) is False
    assert fs.read_claim(c) == {"machine": "216", "at": "x"}


def test_read_claim_empty_or_half_file_is_none(root):
    """回歸 I-2（2026-08-03）：O_EXCL 建檔與寫 JSON 之間有窗——空檔／半截＝壞 claim，回 None 讓上層清。"""
    c = root / "e.claim"
    c.write_text("", encoding="utf-8")
    assert fs.read_claim(c) is None
    c.write_text('{"machine": ', encoding="utf-8")
    assert fs.read_claim(c) is None
    assert fs.read_claim(root / "missing.claim") is None


def test_release_idempotent(root):
    c = root / "r.claim"
    fs.try_claim(c, {})
    fs.release(c)
    fs.release(c)
    assert not c.exists()


# ── 陳舊判定 ────────────────────────────────────────────────────────────────
def test_is_stale_with_injected_now_and_missing_file_true(root):
    f = root / "hb"
    fs.touch(f)
    t0 = fs.mtime(f)
    assert fs.is_stale(f, 60, now=t0 + 30) is False
    assert fs.is_stale(f, 60, now=t0 + 61) is True
    assert fs.is_stale(root / "missing", 60) is True
    assert fs.mtime(root / "missing") is None


def test_touch_updates_mtime(root):
    f = root / "t"
    fs.touch(f)
    os.utime(f, (1_000_000, 1_000_000))
    fs.touch(f)
    assert fs.mtime(f) > 1_000_000


def test_newest_mtime_over_dir(root):
    d = root / "results"
    assert fs.newest_mtime(d) is None
    d.mkdir()
    assert fs.newest_mtime(d) is None
    (d / "a.json").write_text("1", encoding="utf-8")
    os.utime(d / "a.json", (1_000_000, 1_000_000))
    (d / "b.json").write_text("2", encoding="utf-8")
    os.utime(d / "b.json", (2_000_000, 2_000_000))
    assert fs.newest_mtime(d) == 2_000_000
    assert fs.newest_mtime(d, "a*") == 1_000_000


def test_release_retries_on_permission_error(root, monkeypatch):
    """回歸 review-6：Windows 上別的行程開著檔時 unlink 會 PermissionError（sharing violation）——退避重試，用盡才拋。"""
    f = root / "c.claim"
    fs.try_claim(f, {})
    calls = {"n": 0}
    real = os.unlink

    def flaky(path, *a, **k):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError("sharing violation")
        return real(path, *a, **k)

    monkeypatch.setattr(fs.os, "unlink", flaky)
    monkeypatch.setattr(fs.time, "sleep", lambda s: None)
    fs.release(f)
    assert not f.exists() and calls["n"] == 3
    fs.try_claim(f, {})
    monkeypatch.setattr(fs.os, "unlink", lambda *a, **k: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(fs.FsBusy):
        fs.release(f)


# ── jsonl ───────────────────────────────────────────────────────────────────
def test_append_jsonl_read_jsonl_order_and_blank_lines(root):
    p = root / "ev" / "events.jsonl"
    fs.append_jsonl(p, {"ev": "a", "n": 1})
    fs.append_jsonl(p, {"ev": "b", "名": "值"})
    with open(p, "a", encoding="utf-8") as f:
        f.write("\n\n")
    fs.append_jsonl(p, {"ev": "c"})
    assert fs.read_jsonl(p) == [{"ev": "a", "n": 1}, {"ev": "b", "名": "值"}, {"ev": "c"}]
    assert fs.read_jsonl(root / "missing.jsonl") == []
    raw = p.read_text(encoding="utf-8")
    assert "名" in raw, "ensure_ascii=False：人要能直接讀"


# ── 目錄清掃 ────────────────────────────────────────────────────────────────
def test_sweep_dirs_removes_only_children_of_given_root(root):
    """回歸 I-1（2026-07-15）：中斷殘留的工作目錄吃滿系統碟。啟動清掃只掃指定根下的子目錄，不碰檔、不碰外面。"""
    work, other = root / "work", root / "other"
    (work / "s1" / "HFSS").mkdir(parents=True)
    (work / "s2").mkdir()
    (work / "note.txt").write_text("keep", encoding="utf-8")
    (other / "s3").mkdir(parents=True)
    removed = fs.sweep_dirs(work)
    assert sorted(p.name for p in removed) == ["s1", "s2"]
    assert (work / "note.txt").exists() and (other / "s3").exists()
    assert fs.sweep_dirs(root / "missing") == []


def test_read_claim_retries_on_permission_error(tmp_path, monkeypatch):
    """檢查 #5：別的行程正開著 claim 檔時 read_text 拋 PermissionError——退避重試（比照 read_json），不是回 None 當「壞 claim」。"""
    from pathlib import Path
    p = tmp_path / "c.claim"
    p.write_text('{"owner": "a"}', encoding="utf-8")
    calls, real = {"n": 0}, Path.read_text

    def flaky(self, *a, **k):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError("busy")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", flaky)
    monkeypatch.setattr(fs.time, "sleep", lambda s: None)
    assert fs.read_claim(p) == {"owner": "a"} and calls["n"] == 3
