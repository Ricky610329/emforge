# -*- coding: utf-8 -*-
"""tests/test_queue.py — `emforge/queue.py`：共用佇列、原子認領、壞／陳 claim 接管、fail 跨機接管、requeue、STOP。

防什麼：I-2（殭屍／壞 claim）、I-3（jobs.json 並發）、釘選比對錯（2026-08-03 末段 vs 完整 IP）。
"""
import os
import threading
import time

import numpy as np
import pytest

from emforge import batches, fs, model, paths, queue, testing

P = testing.FAKE_PROFILE


def _batch(root, store, n=2, seed=0):
    pats = np.random.default_rng(seed).random((n, *P.shape)) > 0.5
    pats[:, P.fixed_on] = True
    ids = [model.record_id(p, P.name) for p in pats]
    batches.Batch(root, store).write(dict(store=store, sim_profile=P.name, profile_hash=P.profile_hash, strategy="blind",
                                          tick=1, seed=seed, prio=5, kind="sample",
                                          items=[dict(id=i, parent=None, arm="blind", note={}) for i in ids]), pats, ids)
    return ids


def _job(store, prio=5, machine=None):
    return model.Job(store=store, sim_profile=P.name, profile_hash=P.profile_hash, prio=prio, n=2, machine=machine)


def _q(root, *stores_prio):
    q = queue.Queue(root)
    for store, prio in stores_prio:
        _batch(root, store)
        q.add(_job(store, prio))
    return q


def _age(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


# ── add / list ──────────────────────────────────────────────────────────────
def test_add_writes_job_rejects_duplicate_store_and_missing_batch(root):
    q = queue.Queue(root)
    with pytest.raises(queue.MissingBatch):
        q.add(_job("nobatch"))
    _batch(root, "s1")
    q.add(_job("s1", prio=3))
    assert [j.store for j in q.list()] == ["s1"] and q.list()[0].at
    with pytest.raises(queue.DuplicateStore):
        q.add(_job("s1"))
    assert q.state("s1") == "queued" and q.state("nope") == "missing"


def test_concurrent_add_loses_no_job(root):
    """回歸 I-3（2026-07-22／07-24）：多方同時 jobs-add 壞檔。8 執行緒各加 5 筆，全部在、檔完整、鎖釋放。"""
    q = queue.Queue(root)
    stores = [f"s{t}_{i}" for t in range(8) for i in range(5)]
    for s in stores:
        _batch(root, s)
    errors = []

    def add(t):
        try:
            for i in range(5):
                q.add(_job(f"s{t}_{i}"))
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    ts = [threading.Thread(target=add, args=(t,)) for t in range(8)]
    for th in ts:
        th.start()
    for th in ts:
        th.join()
    assert not errors
    assert sorted(j.store for j in q.list()) == sorted(stores)
    assert not paths.jobs_lock(root).exists()


# ── pick ────────────────────────────────────────────────────────────────────
def test_pick_orders_by_prio_skips_done(root):
    q = _q(root, ("low", 9), ("high", 1), ("mid", 5))
    assert q.pick("216").store == "high"
    q.mark_done("high", "216", n_done=2, n_error=0, error_ids=[])
    assert q.state("high") == "done"
    assert q.pick("216").store == "mid"
    assert q.pick("218").store == "low"
    assert q.pick("37") is None


def test_pick_pinned_tag_exact_match_only(root):
    """釘選＝tag 完全相等（2026-08-03：末段 vs 完整 IP 比對錯 → 全機略過）。"""
    q = queue.Queue(root)
    _batch(root, "pinned")
    q.add(_job("pinned", machine="216"))
    assert q.pick("218") is None and q.pick("16") is None and q.pick("2160") is None
    assert q.pick("216").store == "pinned"


def test_pick_claims_atomically_second_worker_none(root):
    q = _q(root, ("only", 5))
    assert q.pick("216").store == "only"
    assert q.pick("218") is None
    assert q.state("only") == "claimed" and q.claim_owner("only") == "216"


def test_pick_resumes_own_claim_after_restart(root):
    q = _q(root, ("mine", 5))
    q.pick("216")
    again = q.pick("216")
    assert again is not None and again.store == "mine" and q.claim_owner("mine") == "216"


def test_pick_clears_ownerless_claim_older_than_60s(root):
    """回歸 I-2（2026-08-03）：O_EXCL 建檔與寫 JSON 之間 worker 死了 → 空 claim 沒人接。>60s 的無主 claim 自動清。"""
    q = _q(root, ("s", 5))
    c = paths.claim_file(root, "s")
    c.parent.mkdir(parents=True, exist_ok=True)
    c.write_text("", encoding="utf-8")
    assert q.pick("216") is None, "新鮮的空 claim 可能正在被寫，先讓一下"
    _age(c, 120)
    assert q.pick("216").store == "s" and q.claim_owner("s") == "216"


def test_pick_takes_over_stale_claim_only_when_results_dir_also_stale(root):
    """回歸 I-2：stale 判定看**進度**（results/ 最新檔）而不只看 claim 時間——跑得慢不等於死了。"""
    q = _q(root, ("s", 5))
    assert q.pick("218").store == "s"
    c = paths.claim_file(root, "s")
    _age(c, 3 * 3600)
    batches.Batch(root, "s").write_result("x", {"id": "x"})       # 218 剛剛還有產出
    assert q.pick("216") is None, "claim 老但有進度 → 不接管"
    _age(paths.batch_result(root, "s", "x"), 3 * 3600)
    assert q.pick("216").store == "s", "claim 老且沒進度 → 接管"
    assert q.claim_owner("s") == "216"


def test_pick_takes_over_fail_when_not_listed_skips_when_listed(root):
    q = _q(root, ("s", 5))
    q.pick("216")
    q.mark_fail("s", "216", reason="連敗熔斷")
    assert q.state("s") == "fail" and not paths.claim_file(root, "s").exists()
    assert q.pick("216") is None, "我死過，讓別台"
    j = q.pick("218")
    assert j is not None and j.store == "s"
    assert fs.read_claim(paths.claim_file(root, "s"))["prior_fail"] == ["216"]
    assert not paths.fail_file(root, "s").exists()
    q.mark_fail("s", "218", reason="又熔斷")
    assert fs.read_json(paths.fail_file(root, "s"))["machines"] == ["216", "218"], "死亡名單累積"
    assert q.pick("218") is None and q.pick("216") is None and q.pick("37").store == "s"


def test_requeue_clears_claim_done_fail_and_restores_job_atomically(root):
    """回歸 I-2（2026-08-31 實例）：清了 .fail 沒清 .claim → 沒人接。requeue 一次處理 claim+done+fail。"""
    q = _q(root, ("s", 5))
    q.pick("216")
    q.mark_done("s", "216", n_done=1, n_error=1, error_ids=["x"])
    paths.fail_file(root, "s").write_text("{}", encoding="utf-8")   # 髒狀態：done 與 fail 同時在
    q.requeue("s")
    assert q.state("s") == "queued"
    for f in (paths.claim_file, paths.done_file, paths.fail_file):
        assert not f(root, "s").exists()
    assert q.pick("218").store == "s"
    with pytest.raises(queue.LiveClaim):
        q.requeue("s")                       # 新鮮 claim 正在跑 → 拒（先 stop 那台）
    _age(paths.claim_file(root, "s"), 3 * 3600)
    q.requeue("s")                           # 陳 claim 可以清
    assert q.state("s") == "queued"
    with pytest.raises(queue.MissingJob):
        q.requeue("nope")


def test_take_over_fail_lost_race_returns_none_not_crash(root, monkeypatch):
    """回歸 review-6：兩台同時接管 .fail——A 過了 exists()、B 先 unlink，A 的 read_json 撞 FileNotFoundError。
    A 要當「輸了競賽」跳過，不是整個 worker 炸掉。"""
    q = _q(root, ("s", 5))
    q.pick("216")
    q.mark_fail("s", "216", "dead")
    real = fs.read_json

    def vanish_then_read(path, *a, **k):
        if str(path).endswith("s.fail"):
            paths.fail_file(root, "s").unlink(missing_ok=True)      # B 先接走了
        return real(path, *a, **k)

    monkeypatch.setattr(queue.fs, "read_json", vanish_then_read)
    assert q.pick("218") is None, "輸了競賽：這輪跳過、不炸"
    monkeypatch.setattr(queue.fs, "read_json", real)
    assert q.pick("218").store == "s", "下一輪照常（.fail 已不在）"


def test_requeue_refuses_when_batch_has_recent_progress_even_if_claim_old(root):
    """回歸 review（砍掉的 queue.py:165）：requeue 只看 claim mtime、claim 又不心跳 → 45 分後活著的批被清掉。
    live＝claim 新鮮 **或** 批有進度。"""
    q = _q(root, ("s", 5))
    q.pick("216")
    _age(paths.claim_file(root, "s"), 3 * 3600)
    batches.Batch(root, "s").write_result("x", {"id": "x"})
    with pytest.raises(queue.LiveClaim):
        q.requeue("s")
    _age(paths.batch_result(root, "s", "x"), 3 * 3600)
    q.requeue("s")
    assert q.state("s") == "queued"


def test_touch_claim_updates_mtime_only_if_exists(root):
    q = _q(root, ("s", 5))
    q.touch_claim("s")                                   # 沒 claim：no-op、不建檔
    assert not paths.claim_file(root, "s").exists()
    q.pick("216")
    _age(paths.claim_file(root, "s"), 3600)
    q.touch_claim("s")
    assert time.time() - fs.mtime(paths.claim_file(root, "s")) < 5
    assert fs.read_claim(paths.claim_file(root, "s"))["machine"] == "216", "只 touch，內容不動"


def test_watch_treats_vanishing_fail_as_not_terminal(root, monkeypatch):
    """回歸 review（砍掉的 queue.py:188）：state() 說 fail、下一瞬間 .fail 被接管刪掉 → mtime None → 不能誤判 FAIL。"""
    q = _q(root, ("s", 5))
    q.pick("216")
    q.mark_fail("s", "216", "dead")
    real_mtime = fs.mtime
    seen = {"n": 0}

    def flaky(path):
        if str(path).endswith("s.fail"):
            seen["n"] += 1
            if seen["n"] == 1:
                return None                              # 被接管的瞬間
        return real_mtime(path)

    monkeypatch.setattr(queue.fs, "mtime", flaky)

    def sleep(s):                                        # 第一輪之後：接管機跑完
        q.pick("218")
        q.mark_done("s", "218", n_done=2, n_error=0, error_ids=[])

    assert q.watch(["s"], poll_s=0, fail_grace_s=0, sleep=sleep) == 0


def test_has_unclaimed_foreground_ignores_background_prio(root):
    q = _q(root, ("bg", 9))
    assert q.has_unclaimed_foreground(background_prio=9) is False
    _batch(root, "fg")
    q.add(_job("fg", prio=3))
    assert q.has_unclaimed_foreground(background_prio=9) is True
    q.pick("216")  # 撿走 fg
    assert q.has_unclaimed_foreground(background_prio=9) is False


def test_stop_request_global_and_per_tag(root):
    q = queue.Queue(root)
    assert q.stop_requested("216") is False
    q.request_stop("216")
    assert q.stop_requested("216") is True and q.stop_requested("218") is False
    q.request_stop()
    assert q.stop_requested("218") is True
    q.clear_stop("216")
    q.clear_stop()
    assert q.stop_requested("216") is False


def test_mark_done_records_summary_and_releases_claim(root):
    q = _q(root, ("s", 5))
    q.pick("216")
    q.mark_done("s", "216", n_done=1, n_error=1, error_ids=["abc"])
    d = fs.read_json(paths.done_file(root, "s"))
    assert d["machine"] == "216" and d["n_done"] == 1 and d["n_error"] == 1 and d["error_ids"] == ["abc"] and d["at"]
    assert not paths.claim_file(root, "s").exists()
