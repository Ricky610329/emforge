# -*- coding: utf-8 -*-
"""tests/runtime/test_dispatch.py — `emforge/runtime/dispatch.py`：去重（db＋inflight＋批內）→ 先寫 inflight → 寫批 → 加 job。

回歸 I-13（2026-08-17）：派工沒掛偵測＝鏈斷睡死；D7：去重無旁路；D10：沒有 keep_work。
"""
import inspect

import numpy as np
import pytest

from emforge import batches, fs, model, paths, queue, testing
from emforge.runtime import dispatch as dp

P = testing.FAKE_PROFILE


def _props(n, seed=0):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        pat = rng.random(P.shape) > 0.5
        pat[P.fixed_on] = True
        out.append(model.Proposal(pattern=pat, parent=None, arm="blind", note={"i": i}))
    return out


def test_dispatch_writes_inflight_batch_job_and_events(rt):
    props = _props(3)
    store = dp.dispatch(rt, "blind", props, tick=1, seed=7, prio=9)
    assert store == "fake_f1-blind-t00001"
    inf = fs.read_json(rt.root / paths.inflight_file("fake_f1", store))
    ids = [model.record_id(p.pattern, P.name) for p in props]
    assert inf["ids"] == ids and inf["collected"] == [] and inf["seed"] == 7 and inf["kind"] == "sample"
    assert inf["items"][ids[1]] == {"parent": None, "arm": "blind", "note": {"i": 1}}
    b = batches.Batch(rt.root, store)
    assert b.ids() == ids and b.manifest()["profile_hash"] == P.profile_hash and b.manifest()["strategy"] == "blind"
    jobs = queue.Queue(rt.root).list()
    assert [j.store for j in jobs] == [store] and jobs[0].prio == 9 and jobs[0].origin == "runtime" and jobs[0].n == 3
    ev = [e["event"] for e in fs.read_jsonl(rt.root / paths.events_jsonl("fake_f1"))]
    assert ev == ["proposals_validated", "batch_dispatched"]


def test_dispatch_writes_inflight_before_job(rt, monkeypatch):
    """回歸 I-13：job 寫失敗時 inflight 已在 → reconcile 揪得到；反過來（job 在、inflight 不在）才是睡死。"""
    def boom(self, job):
        raise RuntimeError("NAS 斷了")

    monkeypatch.setattr(queue.Queue, "add", boom)
    with pytest.raises(RuntimeError):
        dp.dispatch(rt, "blind", _props(2), tick=1, seed=0, prio=9)
    assert (rt.root / paths.inflight_file("fake_f1", "fake_f1-blind-t00001")).exists()
    assert queue.Queue(rt.root).list() == []


def test_dispatch_drops_db_inflight_and_intra_batch_dups_counts_dup_dropped(rt):
    props = _props(4)
    rt.db.add(_record(props[0]))                                  # 已在資料庫
    dp.dispatch(rt, "blind", [props[1]], tick=1, seed=0, prio=9)  # 已 inflight
    store = dp.dispatch(rt, "blind", [props[2], props[2], props[3], props[0], props[1]], tick=2, seed=0, prio=9)
    inf = fs.read_json(rt.root / paths.inflight_file("fake_f1", store))
    assert inf["ids"] == [model.record_id(props[2].pattern, P.name), model.record_id(props[3].pattern, P.name)]
    ev = [e for e in fs.read_jsonl(rt.root / paths.events_jsonl("fake_f1")) if e["event"] == "proposals_validated"]
    assert ev[-1]["n_in"] == 5 and ev[-1]["n_dup"] == 3 and ev[-1]["n_out"] == 2


def test_dispatch_all_dups_returns_none_and_writes_nothing(rt):
    props = _props(2)
    dp.dispatch(rt, "blind", props, tick=1, seed=0, prio=9)
    assert dp.dispatch(rt, "blind", props, tick=2, seed=0, prio=9) is None
    assert not (rt.root / paths.inflight_file("fake_f1", "fake_f1-blind-t00002")).exists()
    assert len(queue.Queue(rt.root).list()) == 1


def test_dispatch_refuses_existing_store_without_writing(rt):
    """回歸 review-3：同一 store 名再派（重播 tick）必須在寫任何東西**之前**拒絕——不能先覆寫 inflight 再撞 BatchExists。"""
    store = dp.dispatch(rt, "blind", _props(2), tick=1, seed=0, prio=9)
    inf_path = rt.root / paths.inflight_file("fake_f1", store)
    inf = fs.read_json(inf_path)
    inf["collected"] = [inf["ids"][0]]
    fs.atomic_write_json(inf_path, inf)
    with pytest.raises(dp.StoreExists):
        dp.dispatch(rt, "blind", _props(3, seed=9), tick=1, seed=0, prio=9)
    assert fs.read_json(inf_path)["collected"] == [inf["ids"][0]], "既有 inflight 一個 byte 都沒動"
    assert len(queue.Queue(rt.root).list()) == 1
    ev = [e["event"] for e in fs.read_jsonl(rt.root / paths.events_jsonl("fake_f1"))]
    assert ev.count("batch_dispatched") == 1


def test_dispatch_error_records_do_not_block_reproposal(rt):
    props = _props(1)
    rt.db.add(_record(props[0], status="error"))
    assert dp.dispatch(rt, "blind", props, tick=1, seed=0, prio=9) is not None


def test_dispatch_has_no_dedup_bypass_and_no_keep_work_param():
    """D7／D10：結構性紅線——簽名裡沒有 skip_dedup／keep_work；kind 只有 sample／repeat。"""
    params = inspect.signature(dp.dispatch).parameters
    assert not any("keep" in p or "skip" in p or "bypass" in p for p in params), list(params)
    assert "kind" in params and params["kind"].default == model.KIND_SAMPLE


def test_dispatch_repeat_kind_skips_dedup_and_uses_given_store(rt):
    """公證重測：同 id 已在 db，還要再量 ×n——只有 runtime 的 notarize 會這樣呼叫。"""
    p = _props(1)[0]
    rt.db.add(_record(p))
    rid = model.record_id(p.pattern, P.name)
    s1 = dp.dispatch(rt, "notarize", [model.Proposal(pattern=p.pattern, parent=rid)], tick=3, seed=0, prio=1,
                     kind=model.KIND_REPEAT, store=paths.notarize_store_name("fake_f1", 3, rid, 1))
    s2 = dp.dispatch(rt, "notarize", [model.Proposal(pattern=p.pattern, parent=rid)], tick=3, seed=0, prio=1,
                     kind=model.KIND_REPEAT, store=paths.notarize_store_name("fake_f1", 3, rid, 2))
    assert s1 == f"fake_f1-notarize-t00003-{rid[:8]}-r1" and s2.endswith("-r2")
    assert fs.read_json(rt.root / paths.inflight_file("fake_f1", s1))["kind"] == "repeat"
    assert [j.prio for j in queue.Queue(rt.root).list()] == [1, 1]


def test_dispatch_rejects_sample_kind_with_explicit_store_or_bad_kind(rt):
    with pytest.raises(ValueError):
        dp.dispatch(rt, "blind", _props(1), tick=1, seed=0, prio=9, kind="weird")


def _record(p, status="done"):
    rid = model.record_id(p.pattern, P.name)
    return model.Record(id=rid, sim_profile=P.name, bits=p.pattern, response=None if status == "error" else np.zeros((2, 17), np.float32),
                        measure={}, score=None if status == "error" else -1.0, status=status, strategy="blind", arm="blind",
                        parent=None, tick=0, seed=0, note={}, kind="sample",
                        run={"store": "old", "machine": "x", "worker_ver": "v", "profile_hash": P.profile_hash, "time_s": 1.0})
