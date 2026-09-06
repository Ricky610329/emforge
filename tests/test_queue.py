# -*- coding: utf-8 -*-
"""tests/test_queue.py — `emforge/queue.py`：共用佇列、原子認領、壞／陳 claim 接管、fail 跨機接管、requeue、STOP。

防什麼：I-2（殭屍／壞 claim）、I-3（jobs.json 並發）、釘選比對錯（2026-08-03 末段 vs 完整 IP）、
接管競賽互刪新 claim（M12c：破鎖不是仲裁，claim 才是）。
"""
import threading
import time

import numpy as np
import pytest

from emforge import batches, model, paths, queue, testing
from emforge.depot import MemoryDepot

P = testing.FAKE_PROFILE


def _batch(depot, store, n=2, seed=0):
    pats = np.random.default_rng(seed).random((n, *P.shape)) > 0.5
    pats[:, P.fixed_on] = True
    ids = [model.record_id(p, P.name) for p in pats]
    batches.Batch(depot, store).write(dict(store=store, sim_profile=P.name, profile_hash=P.profile_hash, strategy="blind",
                                           tick=1, seed=seed, prio=5, kind="sample",
                                           items=[dict(id=i, parent=None, arm="blind", note={}) for i in ids]), pats, ids)
    return ids


def _job(store, prio=5, machine=None):
    return model.Job(store=store, sim_profile=P.name, profile_hash=P.profile_hash, prio=prio, n=2, machine=machine)


def _q(depot, *stores_prio):
    q = queue.Queue(depot)
    for store, prio in stores_prio:
        _batch(q.depot, store)
        q.add(_job(store, prio))
    return q


def _age(q, key, seconds):
    q.depot.set_modified_at(key, q.depot.now() - seconds)


# ── add / list ──────────────────────────────────────────────────────────────
def test_add_writes_job_rejects_duplicate_store_and_missing_batch(root):
    q = queue.Queue(root)
    with pytest.raises(queue.MissingBatch):
        q.add(_job("nobatch"))
    _batch(q.depot, "s1")
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
        _batch(q.depot, s)
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
    assert not q.depot.exists(paths.jobs_lock())


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
    _batch(q.depot, "pinned")
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
    c = root / paths.claim_file("s")                     # 直接在磁碟上放一個空 claim（FileDepot 佈局）
    c.parent.mkdir(parents=True, exist_ok=True)
    c.write_text("", encoding="utf-8")
    assert q.pick("216") is None, "新鮮的空 claim 可能正在被寫，先讓一下"
    _age(q, paths.claim_file("s"), 120)
    assert q.pick("216").store == "s" and q.claim_owner("s") == "216"


def test_pick_takes_over_stale_claim_only_when_results_dir_also_stale(root):
    """回歸 I-2：stale 判定看**進度**（results/ 最新檔）而不只看 claim 時間——跑得慢不等於死了。"""
    q = _q(root, ("s", 5))
    assert q.pick("218").store == "s"
    _age(q, paths.claim_file("s"), 3 * 3600)
    batches.Batch(q.depot, "s").write_result("x", {"id": "x"})       # 218 剛剛還有產出
    assert q.pick("216") is None, "claim 老但有進度 → 不接管"
    _age(q, paths.batch_result("s", "x"), 3 * 3600)
    assert q.pick("216").store == "s", "claim 老且沒進度 → 接管"
    assert q.claim_owner("s") == "216"


def test_pick_takes_over_fail_when_not_listed_skips_when_listed(root):
    q = _q(root, ("s", 5))
    q.pick("216")
    q.mark_fail("s", "216", reason="連敗熔斷")
    assert q.state("s") == "fail" and not q.depot.exists(paths.claim_file("s"))
    assert q.pick("216") is None, "我死過，讓別台"
    j = q.pick("218")
    assert j is not None and j.store == "s"
    assert q.depot.owner(paths.claim_file("s"))["prior_fail"] == ["216"]
    assert not q.depot.exists(paths.fail_file("s"))
    q.mark_fail("s", "218", reason="又熔斷")
    assert q.depot.get_json(paths.fail_file("s"))["machines"] == ["216", "218"], "死亡名單累積"
    assert q.pick("218") is None and q.pick("216") is None and q.pick("37").store == "s"


def test_requeue_clears_claim_done_fail_and_restores_job_atomically(root):
    """回歸 I-2（2026-08-31 實例）：清了 .fail 沒清 .claim → 沒人接。requeue 一次處理 claim+done+fail。"""
    q = _q(root, ("s", 5))
    q.pick("216")
    q.mark_done("s", "216", n_done=1, n_error=1, error_ids=["x"])
    q.depot.put_json(paths.fail_file("s"), {})           # 髒狀態：done 與 fail 同時在
    q.requeue("s")
    assert q.state("s") == "queued"
    for f in (paths.claim_file, paths.done_file, paths.fail_file):
        assert not q.depot.exists(f("s"))
    assert q.pick("218").store == "s"
    with pytest.raises(queue.LiveClaim):
        q.requeue("s")                       # 新鮮 claim 正在跑 → 拒（先 stop 那台）
    _age(q, paths.claim_file("s"), 3 * 3600)
    q.requeue("s")                           # 陳 claim 可以清
    assert q.state("s") == "queued"
    with pytest.raises(queue.MissingJob):
        q.requeue("nope")


def test_take_over_fail_lost_race_returns_none_not_crash(root, monkeypatch):
    """回歸 review-6：兩台同時接管 .fail——A 讀到名單、B 先刪掉。A 的 delete() 回 False＝輸了競賽 → 跳過，不炸、不 claim。"""
    q = _q(root, ("s", 5))
    q.pick("216")
    q.mark_fail("s", "216", "dead")
    real = q.depot.delete

    def b_took_it_first(key):
        if key == paths.fail_file("s"):
            real(key)                                    # B 先接走了
            return False                                 # A 自己的 delete 什麼都沒刪到
        return real(key)

    monkeypatch.setattr(q.depot, "delete", b_took_it_first)
    assert q.pick("218") is None, "輸了競賽：這輪跳過、不炸"
    assert not q.depot.exists(paths.claim_file("s")), "輸家不 claim"
    monkeypatch.setattr(q.depot, "delete", real)
    assert q.pick("218").store == "s", "下一輪照常（.fail 已不在）"


def test_take_over_fail_does_not_force_release_fresh_claim_of_third_machine():
    """M12c：舊碼接走 .fail 後**強制**釋放 claim——第三台剛接管、正在跑的新鮮 claim 會被刪掉。
    現在：新鮮 claim 先判「別人活著」→ 讓；.fail 都不碰。"""
    q = _q(MemoryDepot(), ("s", 5))
    q.depot.put_json(paths.fail_file("s"), {"machines": ["216"], "last": "dead", "at": "x"})
    assert q.depot.claim(paths.claim_file("s"), {"owner": "37", "at": "x", "prior_fail": ["216"]})
    assert q.pick("218") is None
    assert q.claim_owner("s") == "37", "37 的新鮮 claim 一根毛都不能動"
    assert q.depot.exists(paths.fail_file("s")), "沒接管就不消耗 .fail"


def test_pick_two_takeover_racers_never_delete_each_others_fresh_claim():
    """M12c：兩台同判 takeover（都看到陳 claim）。A 破鎖並 claim 後，B 的 break_if_stale 面對的是 A 的**新鮮** claim
    → 沒破到 → B 讓，不會像舊碼那樣 release 掉 A 剛建好的 claim。"""
    q = _q(MemoryDepot(), ("s", 5))
    assert q.depot.claim(paths.claim_file("s"), {"owner": "dead", "at": "x", "prior_fail": []})
    _age(q, paths.claim_file("s"), 3 * 3600)
    job = q.list()[0]
    now = q.depot.now()
    stale = queue.DEFAULT_STALE_S
    verdict_b = q._claim_verdict(paths.claim_file("s"), "s", "218", stale, now)
    assert verdict_b == ("takeover", stale), "B 判定時 claim 還是陳的"
    assert q._claim(job, "216", stale, now) is True and q.claim_owner("s") == "216", "A 先動手：接管成功"
    real = q._claim_verdict
    q._claim_verdict = lambda *a, **k: verdict_b          # B 帶著舊判定動手
    try:
        assert q._claim(job, "218", stale, now) is False
    finally:
        q._claim_verdict = real
    assert q.claim_owner("s") == "216", "A 的新鮮 claim 完好"
    assert q.pick("218") is None, "B 重新判定：A 活著 → 讓"


def test_release_with_machine_tag_only_releases_own_claim():
    q = _q(MemoryDepot(), ("s", 5))
    q.pick("216")
    assert q.release("s", "218") is False and q.claim_owner("s") == "216", "不是我的不放"
    assert q.release("s", "216") is True and q.state("s") == "queued"
    assert q.release("s") is True, "缺＝已經沒有（冪等）"


def test_requeue_refuses_when_batch_has_recent_progress_even_if_claim_old(root):
    """回歸 review（砍掉的 queue.py:165）：requeue 只看 claim mtime、claim 又不心跳 → 45 分後活著的批被清掉。
    live＝claim 新鮮 **或** 批有進度。"""
    q = _q(root, ("s", 5))
    q.pick("216")
    _age(q, paths.claim_file("s"), 3 * 3600)
    batches.Batch(q.depot, "s").write_result("x", {"id": "x"})
    with pytest.raises(queue.LiveClaim):
        q.requeue("s")
    _age(q, paths.batch_result("s", "x"), 3 * 3600)
    q.requeue("s")
    assert q.state("s") == "queued"


def test_touch_claim_updates_modified_at_only_if_exists(root):
    q = _q(root, ("s", 5))
    q.touch_claim("s")                                   # 沒 claim：no-op、不建檔
    assert not q.depot.exists(paths.claim_file("s"))
    q.pick("216")
    _age(q, paths.claim_file("s"), 3600)
    q.touch_claim("s")
    assert time.time() - q.depot.modified_at(paths.claim_file("s")) < 5
    assert q.depot.owner(paths.claim_file("s"))["owner"] == "216", "只 touch，內容不動"


def test_watch_treats_vanishing_fail_as_not_terminal(root, monkeypatch):
    """回歸 review（砍掉的 queue.py:188）：state() 說 fail、下一瞬間 .fail 被接管刪掉 → modified_at None → 不能誤判 FAIL。"""
    q = _q(root, ("s", 5))
    q.pick("216")
    q.mark_fail("s", "216", "dead")
    real_mtime = q.depot.modified_at
    seen = {"n": 0}

    def flaky(key):
        if key == paths.fail_file("s"):
            seen["n"] += 1
            if seen["n"] == 1:
                return None                              # 被接管的瞬間
        return real_mtime(key)

    monkeypatch.setattr(q.depot, "modified_at", flaky)

    def sleep(s):                                        # 第一輪之後：接管機跑完
        q.pick("218")
        q.mark_done("s", "218", n_done=2, n_error=0, error_ids=[])

    assert q.watch(["s"], poll_s=0, fail_grace_s=0, sleep=sleep) == 0


def test_has_unclaimed_foreground_ignores_background_prio(root):
    q = _q(root, ("bg", 9))
    assert q.has_unclaimed_foreground(background_prio=9) is False
    _batch(q.depot, "fg")
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
    d = q.depot.get_json(paths.done_file("s"))
    assert d["machine"] == "216" and d["n_done"] == 1 and d["n_error"] == 1 and d["error_ids"] == ["abc"] and d["at"]
    assert not q.depot.exists(paths.claim_file("s"))


def test_queue_runs_the_same_on_memory_depot():
    """契約的用處：整條認領→終態流程換到 MemoryDepot 一行不改。"""
    q = _q(MemoryDepot(), ("a", 5), ("b", 1))
    assert q.pick("216").store == "b" and q.pick("218").store == "a" and q.pick("37") is None
    q.mark_done("b", "216", n_done=2, n_error=0, error_ids=[])
    q.mark_fail("a", "218", "dead")
    assert q.state("b") == "done" and q.state("a") == "fail" and q.pick("37").store == "a"
