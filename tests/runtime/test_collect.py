# -*- coding: utf-8 -*-
"""tests/runtime/test_collect.py — `emforge/runtime/collect.py`：增量收結果 → profile_hash 比對 → 量測 → 評分 → 入庫 → 收尾 inflight。

回歸 I-10（只拉不重啟＝舊程式量的）：profile_hash 不符 → profile_tamper、不入庫。
"""
import numpy as np
import pytest

from emforge import batches, fs, model, paths, queue, testing
from emforge.runtime import collect as col
from emforge.runtime import dispatch as dp
from tests.runtime.test_dispatch import _props

P = testing.FAKE_PROFILE


def _dispatched(rt, n=3, tick=1, prio=9):
    store = dp.dispatch(rt, "blind", _props(n, seed=tick), tick=tick, seed=tick, prio=prio)
    return store, batches.Batch(rt.root, store), fs.read_json(rt.root / paths.inflight_file("fake_f1", store))["ids"]


def test_postprocessing_errors_count_after_incremental_collect_and_restart(rt, monkeypatch):
    """回歸 I-5（2026-09-12）：防止後處理全部失敗卻不觸發 profile 錯誤率暫停。"""
    from emforge import specs
    from tests.runtime.conftest import make_rt
    store, batch, ids = _dispatched(rt, n=3)
    for rid in ids:
        batch.write_result(rid, _fake_result(rid))

    def broken_measure(*args):
        raise ValueError("broken measurement")

    monkeypatch.setattr(specs, "measure", broken_measure)
    assert all(r.status == "error" for r in col.collect(rt))
    assert rt.state["paused_profile"] is None, "批尚未終結"
    restored = make_rt(rt.root)
    restored.queue.mark_done(store, "216", n_done=3, n_error=0, error_ids=[])
    assert col.collect(restored) == [], "先前已入庫，不重算量測"
    assert restored.state["paused_profile"]["error_rate"] == 1.0
    event = _events(restored, "batch_done")[-1]
    assert event["n_done"] == 0 and event["n_error"] == 3


def test_collect_measures_scores_adds_records_clears_inflight_on_done(rt):
    store, b, ids = _dispatched(rt)
    testing.run_all_jobs(rt.root)
    new = col.collect(rt)
    assert sorted(r.id for r in new) == sorted(ids)
    r = new[0]
    assert r.status == "done" and set(r.measure) == {"m1", "m2", "m3", "m4"} and isinstance(r.score, float)
    assert r.strategy == "blind" and r.arm == "blind" and r.tick == 1 and r.seed == 1 and r.kind == "sample"
    assert r.run["store"] == store and r.run["machine"] == "216" and r.run["profile_hash"] == P.profile_hash
    assert rt.db.ids("fake_f1") == set(ids)
    assert not (rt.root / paths.inflight_file("fake_f1", store)).exists(), "批終態且全收 → inflight 清掉"
    ev = [e["event"] for e in fs.read_jsonl(rt.root / paths.events_jsonl("fake_f1"))]
    assert ev.count("record_added") == 3 and ev[-1] == "batch_done"


def test_collect_is_incremental_and_idempotent(rt):
    store, b, ids = _dispatched(rt)
    b.write_result(ids[0], _fake_result(ids[0]))       # worker 才寫了一筆、批還在跑
    new1 = col.collect(rt)
    assert [r.id for r in new1] == [ids[0]]
    inf = fs.read_json(rt.root / paths.inflight_file("fake_f1", store))
    assert inf["collected"] == [ids[0]], "inflight 記住已收的"
    assert col.collect(rt) == [], "沒新檔就沒新紀錄"
    testing.run_all_jobs(rt.root)
    new2 = col.collect(rt)
    assert sorted(r.id for r in new2) == sorted(ids[1:]), "只收剩下的"


def test_collect_rejects_profile_hash_mismatch_profile_tamper(rt):
    """回歸 I-10：結果自帶量它的儀器指紋；不符＝有人改了 registry／舊程式在跑 → 不入庫、留事件。"""
    store, b, ids = _dispatched(rt, n=2)
    b.write_result(ids[0], _fake_result(ids[0], profile_hash="deadbeef0000"))
    b.write_result(ids[1], _fake_result(ids[1]))
    new = col.collect(rt)
    assert [r.id for r in new] == [ids[1]]
    assert rt.db.ids("fake_f1") == {ids[1]}
    ev = [e for e in fs.read_jsonl(rt.root / paths.events_jsonl("fake_f1")) if e["event"] == "profile_tamper"]
    assert ev and ev[0]["got"] == "deadbeef0000" and ev[0]["expected"] == P.profile_hash
    assert fs.read_json(rt.root / paths.inflight_file("fake_f1", store))["collected"] == sorted(ids), "不再重讀那筆"


def test_collect_stores_error_records_none_response(rt):
    store, b, ids = _dispatched(rt, n=2)
    b.write_result(ids[0], {"id": ids[0], "status": "error", "error": "FakeFailure: x", "attempts": 3, "machine": "216",
                            "worker_ver": "v", "profile_hash": P.profile_hash, "at": "t"})
    b.write_result(ids[1], _fake_result(ids[1]))
    q = queue.Queue(rt.root)
    q.pick("216")
    q.mark_done(store, "216", n_done=1, n_error=1, error_ids=[ids[0]])   # error 要等批 done 才收（review-1）
    new = {r.id: r for r in col.collect(rt)}
    r = new[ids[0]]
    assert r.status == "error" and r.response is None and r.score is None and r.measure == {}
    assert r.note["error"] == "FakeFailure: x" and r.run["time_s"] is None
    assert rt.db.ids("fake_f1") == {ids[1]}, "error 不算已量（可再提案）"
    assert rt.db.ids("fake_f1", status=("error",)) == {ids[0]}


def test_collect_reports_failed_batch_but_keeps_inflight(rt):
    store, b, ids = _dispatched(rt, n=2)
    q = queue.Queue(rt.root)
    q.pick("216")
    b.write_result(ids[0], _fake_result(ids[0]))
    q.mark_fail(store, "216", "熔斷")
    new = col.collect(rt)
    assert [r.id for r in new] == [ids[0]]
    assert (rt.root / paths.inflight_file("fake_f1", store)).exists(), "fail 不是終態（review-2）"
    ev = [e for e in fs.read_jsonl(rt.root / paths.events_jsonl("fake_f1")) if e["event"] == "batch_failed"]
    assert ev and ev[0]["store"] == store and "1/2" in ev[0]["reason"]


def test_collect_pauses_profile_on_error_rate(rt):
    store, b, ids = _dispatched(rt, n=4)
    ids_set = set(ids)
    testing.run_all_jobs(rt.root, sim_factory=lambda wd, p: testing.FakeSimulator(workdir=str(wd), profile=p, fail_ids=ids_set))
    col.collect(rt)
    assert rt.state["paused_profile"] is not None and rt.state["paused_profile"]["store"] == store
    ev = [e for e in fs.read_jsonl(rt.root / paths.events_jsonl("fake_f1")) if e["event"] == "profile_paused"]
    assert ev and ev[0]["error_rate"] == 1.0


def test_collect_uses_conservative_min_for_repeat_records_too(rt):
    """公證重測進 db 的方式與一般紀錄相同（kind=repeat、strategy=notarize），View.top 自然取 min。"""
    p = _props(1, seed=5)[0]
    rid = model.record_id(p.pattern, P.name)
    rt.db.add(_done_record(p, score=-1.0))
    s = dp.dispatch(rt, "notarize", [model.Proposal(pattern=p.pattern, parent=rid)], tick=2, seed=0, prio=1,
                    kind=model.KIND_REPEAT, store=paths.notarize_store_name("fake_f1", 2, rid, 1))
    testing.run_all_jobs(rt.root)
    new = col.collect(rt)
    assert len(new) == 1 and new[0].kind == "repeat" and new[0].strategy == "notarize" and new[0].run["store"] == s
    assert len(rt.db.measurements("fake_f1", rid)) == 2


def _events(rt, name):
    return [e for e in fs.read_jsonl(rt.root / paths.events_jsonl("fake_f1")) if e["event"] == name]


def test_collect_error_waits_for_retry_until_batch_done(rt):
    """回歸 review-1：error 結果在批未 done 前不入庫、不記 collected——補測輪量成功的結果才收得到，
    不會被「已收的 error」擋掉而白燒 100–200 s HFSS。"""
    store, b, ids = _dispatched(rt, n=2)
    q = queue.Queue(rt.root)
    q.pick("216")
    b.write_result(ids[0], _error_result(ids[0], attempts=1))
    assert col.collect(rt) == []
    assert fs.read_json(rt.root / paths.inflight_file("fake_f1", store))["collected"] == []
    assert rt.db.ids("fake_f1", status=("error",)) == set(), "批還在跑：error 不入庫"
    b.write_result(ids[0], {**_fake_result(ids[0]), "attempts": 2})        # 補測輪翻案
    new = col.collect(rt)
    assert [r.id for r in new] == [ids[0]] and new[0].status == "done" and new[0].run["store"] == store
    b.write_result(ids[1], _error_result(ids[1], attempts=3))
    q.mark_done(store, "216", n_done=1, n_error=1, error_ids=[ids[1]])
    new2 = col.collect(rt)
    assert [r.status for r in new2] == ["error"], "批 done 了才把殘留 error 收進 db"
    assert rt.db.ids("fake_f1") == {ids[0]} and rt.db.ids("fake_f1", status=("error",)) == {ids[1]}
    assert not (rt.root / paths.inflight_file("fake_f1", store)).exists()


def test_collect_fail_is_not_terminal_keeps_inflight_reports_once_then_takeover_completes(rt):
    """回歸 review-2：`.fail` 名單外的機器會接管重跑，所以 fail 不是終態——inflight 留著繼續收、batch_failed 只發一次；
    接管機跑完 done 才收尾。"""
    store, b, ids = _dispatched(rt, n=2)
    q = queue.Queue(rt.root)
    q.pick("216")
    b.write_result(ids[0], _fake_result(ids[0]))
    q.mark_fail(store, "216", "熔斷")
    assert [r.id for r in col.collect(rt)] == [ids[0]]
    inf = fs.read_json(rt.root / paths.inflight_file("fake_f1", store))
    assert inf["fail_reported"] is True and inf["collected"] == [ids[0]]
    assert len(_events(rt, "batch_failed")) == 1
    col.collect(rt)
    assert len(_events(rt, "batch_failed")) == 1, "只報一次"
    assert q.pick("218").store == store, "名單外的機器接管"
    b.write_result(ids[1], _fake_result(ids[1]))
    q.mark_done(store, "218", n_done=2, n_error=0, error_ids=[])
    assert [r.id for r in col.collect(rt)] == [ids[1]]
    assert not (rt.root / paths.inflight_file("fake_f1", store)).exists()
    assert _events(rt, "batch_done")[-1]["store"] == store


def test_abandon_collects_errors_removes_inflight_marks_done_and_emits(rt):
    """review-2 的人為出口：沒人接管的 fail 批由人宣告放棄——殘留 error 入庫、inflight 移除、佇列標 done（別台不再接）。"""
    store, b, ids = _dispatched(rt, n=2)
    q = queue.Queue(rt.root)
    q.pick("216")
    b.write_result(ids[0], _error_result(ids[0], attempts=3))
    q.mark_fail(store, "216", "dead")
    col.collect(rt)
    assert rt.db.ids("fake_f1", status=("error",)) == set()
    out = col.abandon(rt, store, by="ricky")
    assert out == {"store": store, "n_collected": 1, "n_missing": 1}
    assert not (rt.root / paths.inflight_file("fake_f1", store)).exists()
    assert rt.db.ids("fake_f1", status=("error",)) == {ids[0]}
    assert q.state(store) == "done" and q.pick("218") is None, "放棄後別台不再接管"
    ev = _events(rt, "batch_abandoned")
    assert ev and ev[0]["by"] == "ricky" and ev[0]["store"] == store
    with pytest.raises(ValueError, match="inflight"):
        col.abandon(rt, store, by="ricky")


def test_abandon_refuses_when_someone_is_running_it(rt):
    store, b, ids = _dispatched(rt, n=2)
    queue.Queue(rt.root).pick("216")
    with pytest.raises(ValueError, match="正在跑"):
        col.abandon(rt, store, by="ricky")
    assert (rt.root / paths.inflight_file("fake_f1", store)).exists()


def test_collect_persists_collected_per_record_and_recovers_record_added_before_crash(rt, monkeypatch):
    """回歸 review-8：每筆入庫後立刻落地 collected；上次死在「add 成功、collected 沒落地」之間的那筆，
    重啟時仍要交給 notarize（add 回 False 也算新收，不能靜默漏掉破榜設計）。"""
    store, b, ids = _dispatched(rt, n=2)
    for i in ids:
        b.write_result(i, _fake_result(i))
    first, second = sorted(ids)
    real_add, calls = rt.db.add, {"n": 0}

    def crash_on_second(rec):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("死在第二筆")
        return real_add(rec)

    monkeypatch.setattr(rt.db, "add", crash_on_second)
    with pytest.raises(RuntimeError):
        col.collect(rt)
    assert fs.read_json(rt.root / paths.inflight_file("fake_f1", store))["collected"] == [first], "第一筆已落地"
    monkeypatch.setattr(rt.db, "add", real_add)
    # 模擬「add 成功但 collected 沒落地」：第二筆先直接進 db，再讓 collect 讀到它
    rec2 = col._to_record(rt, fs.read_json(rt.root / paths.inflight_file("fake_f1", store)), second,
                          b.results()[second], b.patterns())
    assert real_add(rec2) is True
    new = col.collect(rt)
    assert [r.id for r in new] == [second], "已在庫但沒記到 collected → 仍回給 notarize"
    assert len(_events(rt, "record_added")) == 1, "沒有重複 record_added"


def test_run_refreshes_index_at_start(rt_root, rt):
    """review-8b：索引 append 前死掉的檔只有 npz、沒索引行——runtime 啟動 refresh 補回去，去重才看得到它。"""
    from emforge import db as dbm
    from emforge.depot import open_depot
    rec = _done_record(_props(1, seed=77)[0], score=-1.0)
    dbm._save_npz(open_depot(rt_root), paths.record_file("fake_f1", rec.id, "old"), rec)
    assert rec.id not in rt.db.ids("fake_f1")
    assert rt.run(once=True) == 0
    assert rec.id in rt.db.ids("fake_f1")
    assert _events(rt, "index_repaired")[0]["n"] == 1


def _error_result(rid, attempts=1, profile_hash=P.profile_hash):
    return {"id": rid, "status": "error", "error": "FakeFailure: x", "attempts": attempts, "machine": "216",
            "worker_ver": "v", "profile_hash": profile_hash, "at": "t"}


def _fake_result(rid, profile_hash=P.profile_hash):
    return {"id": rid, "status": "done", "response": np.full((2, 17), -12.0).tolist(), "time_s": 1.0,
            "machine": "216", "worker_ver": "v", "profile_hash": profile_hash, "extra": {}, "at": "t", "attempts": 1}


def _done_record(p, score):
    rid = model.record_id(p.pattern, P.name)
    return model.Record(id=rid, sim_profile=P.name, bits=p.pattern, response=np.zeros((2, 17), np.float32),
                        measure={"m1": score}, score=score, status="done", strategy="blind", arm="blind", parent=None,
                        tick=0, seed=0, note={}, kind="sample",
                        run={"store": "old", "machine": "x", "worker_ver": "v", "profile_hash": P.profile_hash, "time_s": 1.0})


def test_abandon_refuses_when_fail_marker_coexists_with_fresh_claim_of_takeover_machine(rt):
    """檢查 #2（2026-09-07）：218 的陳 claim 被 216 接管後，218 的舊行程 mark_fail 寫下 .fail——.fail 與 216 的新鮮 claim 並存，
    `state()` 回 fail；abandon 只看 `state()=="claimed"` 就會放行、把正在量的批標 done、之後的結果沒人收。改用 is_live（與 requeue 同一把尺）。"""
    store, b, ids = _dispatched(rt, n=3)
    q = queue.Queue(rt.root)
    assert q.pick("218").store == store
    q.depot.set_modified_at(paths.claim_file(store), q.depot.now() - 3 * 3600)
    assert q.pick("216").store == store, "216 接管"
    q.mark_fail(store, "218", "simulator_open_failed")      # 218 的舊行程：寫 .fail；release 動不了 216 的 claim
    assert q.state(store) == "fail" and q.claim_owner(store) == "216" and q.is_live(store)
    with pytest.raises(ValueError, match="正在跑"):
        col.abandon(rt, store, by="ricky")
    assert (rt.root / paths.inflight_file("fake_f1", store)).exists() and q.claim_owner(store) == "216"
