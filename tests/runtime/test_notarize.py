# -*- coding: utf-8 -*-
"""tests/runtime/test_notarize.py — `emforge/runtime/notarize.py`：破榜候選 → 自動重測 ×n → 一致性 → pending。

鐵則：迴圈不自己加冕——只寫 pending.jsonl，**永不**碰 ledger。唯一能設 kind=repeat 的地方。
"""
import inspect

import numpy as np

from emforge import fs, paths, queue, testing
from emforge.runtime import collect as col
from emforge.runtime import dispatch as dp
from emforge.runtime import notarize as nz
from tests.runtime.test_dispatch import _props

P = testing.FAKE_PROFILE


def _events(rt, name):
    return [e for e in fs.read_jsonl(paths.events_jsonl(rt.root, "fake_f1")) if e["event"] == name]


def _first_batch(rt, n=3):
    dp.dispatch(rt, "blind", _props(n, seed=1), tick=1, seed=1, prio=9)
    testing.run_all_jobs(rt.root)
    return col.collect(rt)


def test_notarize_dispatches_repeat_n_once_for_record_breaker(rt):
    new = _first_batch(rt)
    rt.state["tick"] = 1
    nz.notarize_step(rt, new)
    best = max(new, key=lambda r: r.score)
    cands = _events(rt, "record_candidate")
    assert [c["id"] for c in cands] == [best.id] and cands[0]["prev_best"] is None
    stores = _events(rt, "notarize_dispatched")[0]["stores"]
    assert len(stores) == 2 and all(s.startswith(f"fake_f1-notarize-t00001-{best.id[:8]}-r") for s in stores)
    jobs = queue.Queue(rt.root).list()
    assert sorted(j.store for j in jobs if j.prio == 1) == sorted(stores), "公證 prio 最高"
    assert best.id in rt.state["notarize"]
    nz.notarize_step(rt, new)                                  # 同一批再丟一次
    assert len(_events(rt, "notarize_dispatched")) == 1, "不重複公證"


def test_notarize_pass_appends_pending_never_touches_ledger(rt):
    new = _first_batch(rt)
    rt.state["tick"] = 1
    nz.notarize_step(rt, new)
    best = max(new, key=lambda r: r.score)
    testing.run_all_jobs(rt.root)                              # FakeSimulator 決定性 → 重測一致
    repeats = col.collect(rt)
    assert all(r.kind == "repeat" and r.strategy == "notarize" and r.id == best.id for r in repeats) and len(repeats) == 2
    rt.state["tick"] = 2
    nz.notarize_step(rt, repeats)
    pend = fs.read_jsonl(paths.pending_jsonl(rt.root, "fake_f1"))
    assert len(pend) == 1 and pend[0]["id"] == best.id and pend[0]["conservative"] == best.score and pend[0]["spread"] == 0
    assert len(pend[0]["scores"]) == 3
    assert not list((rt.root / "ledger").rglob("*.json")), "永不碰榜（沒有任何榜檔被寫）"
    assert best.id not in rt.state["notarize"] and _events(rt, "notarize_pass")
    nz.notarize_step(rt, repeats)
    assert len(_events(rt, "record_candidate")) == 1, "重測紀錄本身不會再觸發候選"


def test_notarize_reject_when_spread_exceeds_noise_floor(rt):
    new = _first_batch(rt)
    rt.state["tick"] = 1
    nz.notarize_step(rt, new)

    class _Noisy(testing.FakeSimulator):
        def simulate(self, bits):
            r = super().simulate(bits)
            r.response[:] += np.random.default_rng().normal(0, 20, r.response.shape).astype(np.float32)
            return r

    testing.run_all_jobs(rt.root, sim_factory=lambda wd, p: _Noisy(workdir=str(wd), profile=p))
    repeats = col.collect(rt)
    rt.state["tick"] = 2
    nz.notarize_step(rt, repeats)
    assert fs.read_jsonl(paths.pending_jsonl(rt.root, "fake_f1")) == []
    rej = _events(rt, "notarize_reject")
    assert rej and rej[0]["spread"] > rej[0]["noise_floor"] and rt.state["notarize"] == {}


def test_notarize_threshold_uses_ledger_best_and_pending(rt):
    new = _first_batch(rt)
    best = max(new, key=lambda r: r.score)
    paths.ledger_file(rt.root, "fake_f1", "fake_v1").parent.mkdir(parents=True)
    fs.atomic_write_json(paths.ledger_file(rt.root, "fake_f1", "fake_v1"),
                         {"profile": "fake_f1", "spec": "fake_v1", "best": {"id": "x", "score": best.score + 100}, "history": []})
    rt.state["tick"] = 1
    nz.notarize_step(rt, new)
    assert _events(rt, "record_candidate") == [], "沒破榜就不公證"
    fs.atomic_write_json(paths.ledger_file(rt.root, "fake_f1", "fake_v1"),
                         {"profile": "fake_f1", "spec": "fake_v1", "best": {"id": "x", "score": best.score - 100}, "history": []})
    fs.append_jsonl(paths.pending_jsonl(rt.root, "fake_f1"), {"id": "y", "conservative": best.score + 1})
    nz.notarize_step(rt, new)
    assert _events(rt, "record_candidate") == [], "已有更好的待審 → 不重複公證"


def test_notarize_completes_with_partial_when_repeat_store_fails(rt):
    new = _first_batch(rt)
    rt.state["tick"] = 1
    nz.notarize_step(rt, new)
    stores = _events(rt, "notarize_dispatched")[0]["stores"]
    q = queue.Queue(rt.root)
    j = q.pick("216")
    assert j.store in stores
    q.mark_fail(j.store, "216", "dead")
    q.mark_fail(j.store, "218", "dead")
    q.mark_fail(j.store, "37", "dead")
    testing.run_all_jobs(rt.root, machine_tag="37")            # 另一個 repeat store 正常跑完
    repeats = col.collect(rt)
    assert len(repeats) == 1
    rt.state["tick"] = 2
    nz.notarize_step(rt, repeats)
    assert rt.state["notarize"] == {}, "一個 store 死了、另一個回來了 → 以手上的兩筆判定，不等到天荒地老"
    assert len(fs.read_jsonl(paths.pending_jsonl(rt.root, "fake_f1"))) == 1


def test_notarize_survives_dispatch_failure_and_does_not_register_candidate(rt, monkeypatch):
    """回歸 review-3：公證重測派不出去（NAS／StoreExists）→ 事件、不登記候選（下個 tick 不會卡在等一個不存在的批）。"""
    new = _first_batch(rt)
    rt.state["tick"] = 1

    def boom(*a, **k):
        raise RuntimeError("NAS 斷了")

    monkeypatch.setattr(nz, "dispatch", boom)
    nz.notarize_step(rt, new)                            # 不拋
    ev = _events(rt, "dispatch_failed")
    assert ev and ev[0]["name"] == "notarize"
    assert rt.state["notarize"] == {} and _events(rt, "notarize_dispatched") == []


def test_kind_repeat_only_set_in_notarize_module():
    """D7 紅線：grep 原始碼——KIND_REPEAT 只在 notarize.py 被當作 dispatch 參數。"""
    from emforge.runtime import core, schedule
    for mod in (core, schedule, col):
        assert "KIND_REPEAT" not in inspect.getsource(mod), mod.__name__
    assert "KIND_REPEAT" in inspect.getsource(nz)
