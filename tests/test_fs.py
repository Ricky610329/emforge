# -*- coding: utf-8 -*-
"""tests/test_fs.py — `emforge/fs.py`：檔案系統協調原語（原子寫入、認領、陳舊判定、鎖）。

這是 O-3（協調層假設 O_EXCL 近似原子 + mtime 可信）的唯一落點；換儲存後端只動 fs.py。
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


# ── 鎖 ──────────────────────────────────────────────────────────────────────
def test_lock_serializes_counter_8_threads(root):
    """回歸 I-3（2026-07-22／07-24）：多方同時讀-改-寫 jobs.json 壞檔。持鎖後 8 執行緒累加不丟。"""
    lk, data = root / "q" / "jobs.lock", root / "q" / "counter.json"
    fs.atomic_write_json(data, {"n": 0})

    def bump():
        for _ in range(25):
            with fs.Lock(lk, timeout_s=30):
                d = fs.read_json(data)
                d["n"] += 1
                fs.atomic_write_json(data, d)

    ts = [threading.Thread(target=bump) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert fs.read_json(data)["n"] == 200
    assert not lk.exists(), "鎖釋放乾淨"


def test_lock_breaks_stale_by_rename_not_remove(root):
    """陳鎖（持鎖者已死）自動破——用 rename 而不是 remove：兩個破鎖者不會刪到對方剛建好的新鎖。"""
    lk = root / "l.lock"
    lk.parent.mkdir(parents=True, exist_ok=True)
    fs.try_claim(lk, {"pid": 1})
    os.utime(lk, (time.time() - 1000, time.time() - 1000))
    with fs.Lock(lk, stale_s=180, timeout_s=1) as held:
        assert lk.exists() and held is not None
        broken = [x.name for x in root.iterdir() if ".broken." in x.name]
        assert len(broken) == 1, "陳鎖被改名保留當證據，不是刪掉"
    assert not lk.exists()


def test_two_breakers_only_one_wins(root):
    lk = root / "l2.lock"
    lk.parent.mkdir(parents=True, exist_ok=True)
    fs.try_claim(lk, {"pid": 1})
    os.utime(lk, (time.time() - 1000, time.time() - 1000))
    inside, peak, lock_ = [0], [0], threading.Lock()
    go = threading.Barrier(2)

    def worker():
        go.wait()
        with fs.Lock(lk, stale_s=180, timeout_s=10):
            with lock_:
                inside[0] += 1
                peak[0] = max(peak[0], inside[0])
            time.sleep(0.05)
            with lock_:
                inside[0] -= 1

    ts = [threading.Thread(target=worker) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert peak[0] == 1, "兩人從未同時持鎖"
    assert len([x for x in root.iterdir() if ".broken." in x.name]) == 1, "只有一個破鎖者成功"
    assert not lk.exists()


def test_lock_timeout_raises_locktimeout_not_systemexit(root):
    """函式庫層永不 SystemExit（舊 `_jobs_lock_acquire` 是反例）；逾時是可捕捉的例外。"""
    lk = root / "fresh.lock"
    lk.parent.mkdir(parents=True, exist_ok=True)
    fs.try_claim(lk, {"pid": 99})
    t0 = time.time()
    with pytest.raises(fs.LockTimeout):
        with fs.Lock(lk, stale_s=180, timeout_s=0.3):
            pass
    assert time.time() - t0 < 5
    assert not issubclass(fs.LockTimeout, SystemExit)
    assert lk.exists(), "別人的新鮮鎖不能被動"


def test_lock_times_out_when_claim_keeps_failing_and_file_missing(root, monkeypatch):
    """回歸 review-5：NAS 拒建檔時 try_claim 回 False 且檔不存在 → 舊碼在破鎖分支 continue 跳過逾時檢查與 sleep＝100% CPU 無限迴圈。
    現在每圈都走到逾時檢查：在 timeout_s 內拋 LockTimeout、而且有 sleep。"""
    monkeypatch.setattr(fs, "try_claim", lambda path, payload: False)
    slept = []
    t0 = time.time()
    with pytest.raises(fs.LockTimeout):
        with fs.Lock(root / "never.lock", timeout_s=0.3, sleep=slept.append):
            pass
    assert time.time() - t0 < 5 and slept, "有逾時、有 sleep（不是忙迴圈）"


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


# ── 小工具 ──────────────────────────────────────────────────────────────────
def test_now_iso_format():
    s = fs.now_iso()
    assert len(s) == 19 and s[10] == "T" and s[4] == s[7] == "-" and s[13] == s[16] == ":"


def test_sha1_hex_deterministic_and_order_sensitive():
    assert fs.sha1_hex(b"a", b"b") == fs.sha1_hex(b"a", b"b")
    assert fs.sha1_hex(b"a", b"b") != fs.sha1_hex(b"b", b"a")
    assert len(fs.sha1_hex(b"")) == 40
