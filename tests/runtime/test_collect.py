# -*- coding: utf-8 -*-
"""tests/runtime/test_collect.py — `emforge/runtime/collect.py`：增量收結果 → profile_hash 比對 → 量測 → 評分 → 入庫 → 收尾 inflight。

回歸 I-10（只拉不重啟＝舊程式量的）：profile_hash 不符 → profile_tamper、不入庫。
"""
import numpy as np

from emforge import batches, fs, model, paths, queue, testing
from emforge.runtime import collect as col
from emforge.runtime import dispatch as dp
from tests.runtime.test_dispatch import _props

P = testing.FAKE_PROFILE


def _dispatched(rt, n=3, tick=1, prio=9):
    store = dp.dispatch(rt, "blind", _props(n, seed=tick), tick=tick, seed=tick, prio=prio)
    return store, batches.Batch(rt.root, store), fs.read_json(paths.inflight_file(rt.root, "fake_f1", store))["ids"]


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
    assert not paths.inflight_file(rt.root, "fake_f1", store).exists(), "批終態且全收 → inflight 清掉"
    ev = [e["event"] for e in fs.read_jsonl(paths.events_jsonl(rt.root, "fake_f1"))]
    assert ev.count("record_added") == 3 and ev[-1] == "batch_done"


def test_collect_is_incremental_and_idempotent(rt):
    store, b, ids = _dispatched(rt)
    b.write_result(ids[0], _fake_result(ids[0]))       # worker 才寫了一筆、批還在跑
    new1 = col.collect(rt)
    assert [r.id for r in new1] == [ids[0]]
    inf = fs.read_json(paths.inflight_file(rt.root, "fake_f1", store))
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
    ev = [e for e in fs.read_jsonl(paths.events_jsonl(rt.root, "fake_f1")) if e["event"] == "profile_tamper"]
    assert ev and ev[0]["got"] == "deadbeef0000" and ev[0]["expected"] == P.profile_hash
    assert fs.read_json(paths.inflight_file(rt.root, "fake_f1", store))["collected"] == sorted(ids), "不再重讀那筆"


def test_collect_stores_error_records_none_response(rt):
    store, b, ids = _dispatched(rt, n=2)
    b.write_result(ids[0], {"id": ids[0], "status": "error", "error": "FakeFailure: x", "attempts": 3, "machine": "216",
                            "worker_ver": "v", "profile_hash": P.profile_hash, "at": "t"})
    new = col.collect(rt)
    r = new[0]
    assert r.status == "error" and r.response is None and r.score is None and r.measure == {}
    assert r.note["error"] == "FakeFailure: x" and r.run["time_s"] is None
    assert rt.db.ids("fake_f1") == set(), "error 不算已量（可再提案）"
    assert rt.db.ids("fake_f1", status=("error",)) == {ids[0]}


def test_collect_finalizes_failed_batch_with_batch_failed_event(rt):
    store, b, ids = _dispatched(rt, n=2)
    q = queue.Queue(rt.root)
    q.pick("216")
    b.write_result(ids[0], _fake_result(ids[0]))
    q.mark_fail(store, "216", "熔斷")
    new = col.collect(rt)
    assert [r.id for r in new] == [ids[0]]
    assert not paths.inflight_file(rt.root, "fake_f1", store).exists()
    ev = [e for e in fs.read_jsonl(paths.events_jsonl(rt.root, "fake_f1")) if e["event"] == "batch_failed"]
    assert ev and ev[0]["store"] == store and "1" in ev[0]["reason"]


def test_collect_pauses_profile_on_error_rate(rt):
    store, b, ids = _dispatched(rt, n=4)
    ids_set = set(ids)
    testing.run_all_jobs(rt.root, sim_factory=lambda wd, p: testing.FakeSimulator(workdir=str(wd), profile=p, fail_ids=ids_set))
    col.collect(rt)
    assert rt.state["paused_profile"] is not None and rt.state["paused_profile"]["store"] == store
    ev = [e for e in fs.read_jsonl(paths.events_jsonl(rt.root, "fake_f1")) if e["event"] == "profile_paused"]
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


def _fake_result(rid, profile_hash=P.profile_hash):
    return {"id": rid, "status": "done", "response": np.full((2, 17), -12.0).tolist(), "time_s": 1.0,
            "machine": "216", "worker_ver": "v", "profile_hash": profile_hash, "extra": {}, "at": "t", "attempts": 1}


def _done_record(p, score):
    rid = model.record_id(p.pattern, P.name)
    return model.Record(id=rid, sim_profile=P.name, bits=p.pattern, response=np.zeros((2, 17), np.float32),
                        measure={"m1": score}, score=score, status="done", strategy="blind", arm="blind", parent=None,
                        tick=0, seed=0, note={}, kind="sample",
                        run={"store": "old", "machine": "x", "worker_ver": "v", "profile_hash": P.profile_hash, "time_s": 1.0})
