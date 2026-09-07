# -*- coding: utf-8 -*-
"""tests/depot/test_contract.py — `Depot` 契約：每個後端（FileDepot／MemoryDepot）都要過同一套。

這是「基礎設施解耦」的機器版：協調狀態只經這些原語；換 S3／SQL 後端＝實作原語＋過這套契約，
runtime／worker／db 的邏輯一行不改。語義刻意保守（列舉可最終一致、租約式認領、沒有 rename），三種後端都做得到。
"""
import io
import threading
import time
import uuid

import numpy as np
import pytest

from emforge import fs
from emforge.depot import Depot, FileDepot, MemoryDepot, open_depot


@pytest.fixture(params=["file", "memory"])
def depot(request, root) -> Depot:
    return FileDepot(root) if request.param == "file" else MemoryDepot(name=f"t-{uuid.uuid4().hex[:8]}")


# ── 文件 ────────────────────────────────────────────────────────────────────
def test_put_get_bytes_roundtrip_and_missing_is_none(depot):
    assert depot.get_bytes("a/b.bin") is None
    depot.put_bytes("a/b.bin", b"\x00\x01\xff")
    assert depot.get_bytes("a/b.bin") == b"\x00\x01\xff"
    depot.put_bytes("a/b.bin", b"new")
    assert depot.get_bytes("a/b.bin") == b"new"


def test_put_json_get_json_roundtrip_unicode_sorted_keys(depot):
    depot.put_json("d/x.json", {"z": 1, "名": "值", "a": [1, None, 2.5]})
    assert depot.get_json("d/x.json") == {"z": 1, "名": "值", "a": [1, None, 2.5]}
    raw = depot.get_bytes("d/x.json").decode("utf-8")
    assert "名" in raw and raw.index('"a"') < raw.index('"z"') < raw.index('"名"'), "不轉義中文、鍵排序"
    assert depot.get_json("d/missing.json") is None


def test_put_replaces_atomically_under_concurrent_readers(depot):
    depot.put_json("hot.json", {"i": -1, "t": -1})
    bad, errors, stop = [], [], threading.Event()

    def writer(tid):
        try:
            for i in range(30):
                depot.put_json("hot.json", {"i": i, "t": tid})
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    def reader():
        try:
            while not stop.is_set():
                d = depot.get_json("hot.json")
                if set(d) != {"i", "t"}:
                    bad.append(d)
                time.sleep(0.001)
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    r = threading.Thread(target=reader)
    ws = [threading.Thread(target=writer, args=(t,)) for t in range(3)]
    r.start()
    for w in ws:
        w.start()
    for w in ws:
        w.join()
    stop.set()
    r.join()
    assert not errors and not bad and depot.get_json("hot.json")["i"] == 29


def test_put_json_keeps_old_doc_when_serialization_fails(depot):
    depot.put_json("k.json", {"ok": 1})
    with pytest.raises(TypeError):
        depot.put_json("k.json", {"bad": object()})
    assert depot.get_json("k.json") == {"ok": 1}


def test_require_bytes_raises_filenotfound_when_missing(depot):
    with pytest.raises(FileNotFoundError):
        depot.require_bytes("r/none")
    depot.put_bytes("r/x", b"1")
    assert depot.require_bytes("r/x") == b"1"


def test_require_json_raises_filenotfound_when_missing(depot):
    with pytest.raises(FileNotFoundError):
        depot.require_json("r/none.json")
    depot.put_json("r/x.json", {"a": 1})
    assert depot.require_json("r/x.json") == {"a": 1}


def test_exists_and_delete_true_only_for_the_call_that_removed(depot):
    assert not depot.exists("e/x") and depot.delete("e/x") is False
    depot.put_bytes("e/x", b"1")
    assert depot.exists("e/x")
    assert depot.delete("e/x") is True and depot.delete("e/x") is False
    assert not depot.exists("e/x") and depot.get_bytes("e/x") is None


# ── 列舉 ────────────────────────────────────────────────────────────────────
def test_list_returns_direct_children_and_marks_subprefixes_with_slash(depot):
    assert depot.list("a/") == []
    depot.put_bytes("a/x.json", b"1")
    depot.put_bytes("a/b/y.json", b"2")
    depot.put_bytes("a/b/z.json", b"3")
    depot.put_bytes("other/w.json", b"4")
    assert depot.list("a/") == ["a/b/", "a/x.json"]
    assert depot.list("a/b/") == ["a/b/y.json", "a/b/z.json"]


def test_list_hides_broken_evidence_names(depot):
    """破鎖證據檔（`<name>.broken.<pid>.<rand>`）是後端內部名——兩個後端都不當 key 列出來。"""
    depot.put_bytes("q/x.claim", b"1")
    depot.put_bytes("q/x.claim.broken.1.ab", b"dead")
    assert depot.list("q/") == ["q/x.claim"]


def test_list_reflects_put_and_delete_and_hides_dot_keys(depot):
    depot.put_bytes("a/x.json", b"1")
    depot.put_bytes("a/.hidden", b"h")
    assert depot.list("a/") == ["a/x.json"]
    depot.delete("a/x.json")
    assert depot.list("a/") == []


def test_list_rejects_prefix_without_trailing_slash(depot):
    with pytest.raises(ValueError):
        depot.list("a")


def test_large_npz_bytes_roundtrip(depot):
    buf = io.BytesIO()
    arr = np.random.default_rng(0).random((300, 300)).astype(np.float32)
    np.savez(buf, arr=arr, meta=np.asarray("中文 meta"))
    depot.put_bytes("db/p/x.npz", buf.getvalue())
    with np.load(io.BytesIO(depot.get_bytes("db/p/x.npz")), allow_pickle=False) as z:
        assert np.array_equal(z["arr"], arr) and str(z["meta"]) == "中文 meta"


def test_keys_with_unicode_and_apostrophe_segments_roundtrip(depot):
    key = "db/碩二's_profile/0123456789abcdef-x.json"
    depot.put_json(key, {"k": 1})
    assert depot.get_json(key) == {"k": 1} and key in depot.list("db/碩二's_profile/")


# ── 日誌 ────────────────────────────────────────────────────────────────────
def test_append_then_read_log_preserves_order(depot):
    for i in range(5):
        depot.append("logs/e.jsonl", {"i": i, "名": "值"})
    assert [r["i"] for r in depot.read_log("logs/e.jsonl")] == [0, 1, 2, 3, 4]
    assert depot.read_log("logs/e.jsonl")[1]["名"] == "值"


def test_read_log_missing_is_empty_list(depot):
    assert depot.read_log("logs/nope.jsonl") == []


def test_rewrite_log_replaces_whole_log(depot):
    depot.append("logs/i.jsonl", {"a": 1})
    depot.append("logs/i.jsonl", {"a": 2})
    depot.rewrite_log("logs/i.jsonl", [{"a": 9}])
    assert depot.read_log("logs/i.jsonl") == [{"a": 9}]
    depot.append("logs/i.jsonl", {"a": 10})
    assert [r["a"] for r in depot.read_log("logs/i.jsonl")] == [9, 10]


# ── 租約 ────────────────────────────────────────────────────────────────────
def test_claim_is_exclusive_across_8_threads_one_winner(depot):
    wins, go = [], threading.Barrier(8)

    def worker(i):
        go.wait()
        if depot.claim("q/state/s.claim", {"owner": str(i)}):
            wins.append(i)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(wins) == 1 and depot.owner("q/state/s.claim")["owner"] == str(wins[0])


def test_claim_false_while_held_true_after_release(depot):
    assert depot.claim("l/k", {"owner": "a"}) is True
    assert depot.claim("l/k", {"owner": "b"}) is False
    assert depot.release("l/k", owner="a") is True
    assert depot.claim("l/k", {"owner": "b"}) is True


def test_claim_requires_owner_in_payload(depot):
    with pytest.raises(ValueError):
        depot.claim("l/k", {"machine": "216"})


def test_owner_returns_payload_and_none_when_missing(depot):
    assert depot.owner("l/none") is None
    depot.claim("l/k", {"owner": "216", "prior_fail": ["37"]})
    assert depot.owner("l/k") == {"owner": "216", "prior_fail": ["37"]}


def test_release_with_wrong_owner_refuses_and_keeps_lease(depot):
    depot.claim("l/k", {"owner": "a"})
    assert depot.release("l/k", owner="b") is False
    assert depot.owner("l/k")["owner"] == "a"


def test_release_without_owner_forces_and_missing_is_true(depot):
    depot.claim("l/k", {"owner": "a"})
    assert depot.release("l/k") is True and not depot.exists("l/k")
    assert depot.release("l/k") is True, "缺＝已經沒有＝True（冪等）"


def test_touch_extends_lease_so_break_if_stale_refuses(depot):
    depot.claim("l/k", {"owner": "a"})
    depot.set_modified_at("l/k", depot.now() - 1000)
    assert depot.is_stale("l/k", 60)
    assert depot.touch("l/k") is True
    assert not depot.is_stale("l/k", 60)
    assert depot.break_if_stale("l/k", 60) is False and depot.owner("l/k")["owner"] == "a"


def test_touch_missing_key_is_noop_and_does_not_create(depot):
    assert depot.touch("l/none") is False and not depot.exists("l/none")


def test_break_if_stale_removes_only_when_older_than_stale_s(depot):
    assert depot.break_if_stale("l/none", 60) is False
    depot.claim("l/k", {"owner": "a"})
    assert depot.break_if_stale("l/k", 60) is False and depot.exists("l/k"), "新鮮的不動"
    depot.set_modified_at("l/k", depot.now() - 1000)
    assert depot.break_if_stale("l/k", 60) is True and not depot.exists("l/k")


def test_two_breakers_never_delete_each_others_reclaim_and_claim_arbitrates(depot):
    """破鎖不是仲裁（Windows 兩個 rename 可都成功、S3 版本化 DELETE 冪等）：可能兩人都 True，但只有一人 claim 到，
    而且輸家事後既破不掉、也刪不掉贏家的新租約。"""
    depot.claim("l/k", {"owner": "dead"})
    depot.set_modified_at("l/k", depot.now() - 1000)
    broke, claimed, go = [], [], threading.Barrier(2)

    def breaker(me):
        go.wait()
        if depot.break_if_stale("l/k", 60):
            broke.append(me)
            if depot.claim("l/k", {"owner": me}):
                claimed.append(me)

    ts = [threading.Thread(target=breaker, args=(m,)) for m in ("A", "B")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert 1 <= len(broke) <= 2 and len(claimed) == 1 and claimed[0] in broke
    winner = claimed[0]
    assert depot.owner("l/k")["owner"] == winner
    loser = "B" if winner == "A" else "A"
    assert depot.break_if_stale("l/k", 60) is False, "贏家的新租約是新鮮的，輸家破不掉"
    assert depot.release("l/k", owner=loser) is False and depot.owner("l/k")["owner"] == winner


# ── 時間 ────────────────────────────────────────────────────────────────────
def test_modified_at_none_when_missing_and_nondecreasing_after_put_and_touch(depot):
    assert depot.modified_at("t/x") is None
    depot.put_bytes("t/x", b"1")
    m1 = depot.modified_at("t/x")
    depot.put_bytes("t/x", b"2")
    m2 = depot.modified_at("t/x")
    depot.touch("t/x")
    m3 = depot.modified_at("t/x")
    assert m1 is not None and m1 <= m2 <= m3
    assert abs(depot.now() - m3) < 5


def test_newest_is_max_of_children_and_ignores_subprefixes(depot):
    assert depot.newest("r/") is None
    depot.put_bytes("r/a.json", b"1")
    depot.put_bytes("r/b.json", b"2")
    depot.put_bytes("r/sub/c.json", b"3")
    depot.set_modified_at("r/a.json", 1_000_000)
    depot.set_modified_at("r/b.json", 2_000_000)
    depot.set_modified_at("r/sub/c.json", 3_000_000)
    assert depot.newest("r/") == 2_000_000


def test_is_stale_missing_key_is_stale_and_now_injectable(depot):
    assert depot.is_stale("s/none", 60) is True
    depot.put_bytes("s/x", b"1")
    m = depot.modified_at("s/x")
    assert depot.is_stale("s/x", 60, now=m + 30) is False
    assert depot.is_stale("s/x", 60, now=m + 61) is True


# ── lock（衍生自租約） ───────────────────────────────────────────────────────
def test_lock_serializes_counter_8_threads(depot):
    """I-3 港：8 執行緒持鎖讀改寫同一文件不丟。"""
    depot.put_json("q/counter.json", {"n": 0})

    def bump(i):
        for _ in range(25):
            with depot.lock("q/jobs.lock", owner=f"w{i}", timeout_s=30):
                d = depot.get_json("q/counter.json")
                d["n"] += 1
                depot.put_json("q/counter.json", d)

    ts = [threading.Thread(target=bump, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert depot.get_json("q/counter.json")["n"] == 200 and not depot.exists("q/jobs.lock")


def test_lock_breaks_stale_holder(depot):
    depot.claim("q/l", {"owner": "dead"})
    depot.set_modified_at("q/l", depot.now() - 1000)
    with depot.lock("q/l", owner="me", stale_s=60, timeout_s=2):
        assert depot.owner("q/l")["owner"] == "me"
    assert not depot.exists("q/l")


def test_lock_timeout_raises_locktimeout_not_systemexit(depot):
    depot.claim("q/l", {"owner": "alive"})
    t0 = time.time()
    with pytest.raises(fs.LockTimeout):
        with depot.lock("q/l", owner="me", stale_s=180, timeout_s=0.3):
            pass
    assert time.time() - t0 < 5 and depot.owner("q/l")["owner"] == "alive"
    assert not issubclass(fs.LockTimeout, SystemExit)


def test_lock_times_out_when_claim_keeps_failing_and_key_missing(depot, monkeypatch):
    """review-5 港：claim 一直 False 而 key 又不存在（權限問題）→ 每圈都走逾時檢查＋sleep，不忙迴圈。"""
    monkeypatch.setattr(depot, "claim", lambda key, payload: False)
    slept = []
    with pytest.raises(fs.LockTimeout):
        with depot.lock("q/none", owner="me", stale_s=60, timeout_s=0.2, sleep=lambda s: slept.append(s) or time.sleep(0.01)):
            pass
    assert slept, "有 sleep，不是 100% CPU"


# ── 其他 ────────────────────────────────────────────────────────────────────
def test_selfcheck_returns_no_problems_on_healthy_depot(depot):
    assert depot.selfcheck() == []


def test_ensure_prefixes_then_list_works(depot):
    depot.ensure_prefixes(("db/", "queue/state/"))
    assert depot.list("db/") == [] and depot.list("queue/state/") == []


def test_spec_roundtrips_through_open_depot(depot):
    again = open_depot(depot.spec)
    depot.put_bytes("spec/x", b"1")
    assert again.get_bytes("spec/x") == b"1"
    assert open_depot(depot) is depot


def test_lock_holder_broken_and_reclaimed_does_not_delete_the_new_lock_on_exit(depot):
    """檢查 #45：持鎖者卡太久被破鎖、別人重認領後，原持鎖者離開 with 區塊時 release(owner=) 不能刪到對方的新鎖。"""
    with depot.lock("q/l", owner="A", stale_s=60, timeout_s=1):
        depot.set_modified_at("q/l", depot.now() - 1000)
        assert depot.break_if_stale("q/l", 60) is True
        assert depot.claim("q/l", {"owner": "B"})
    assert depot.owner("q/l")["owner"] == "B"
