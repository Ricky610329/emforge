# -*- coding: utf-8 -*-
"""tests/runtime/test_reconcile.py — `emforge/runtime/reconcile.py`：inflight／佇列／批 三方對帳。

回歸 I-14（2026-07-13）：在未落地的狀態上疊工作。啟動不一致 → 拒 tick。
"""
import pytest

from emforge import fs, model, paths, queue, testing
from emforge.runtime import core, reconcile
from tests.worker.conftest import make_batch, make_job


def test_reconcile_clean_on_fresh_root(rt):
    assert reconcile.reconcile(rt) == []


def test_reconcile_flags_inflight_without_job_and_run_refuses(rt):
    fs.atomic_write_json(paths.inflight_file(rt.root, "fake_f1", "ghost"),
                         {"store": "ghost", "strategy": "blind", "tick": 1, "seed": 0, "kind": "sample", "prio": 9,
                          "ids": [], "items": {}, "collected": [], "at": "x"})
    problems = reconcile.reconcile(rt)
    assert any("ghost" in p and "inflight_without" in p for p in problems)
    assert rt.run(once=True) == 2, "不一致 → exit 2、不 tick"
    ev = [e for e in fs.read_jsonl(paths.events_jsonl(rt.root, "fake_f1")) if e["event"] == "reconcile_mismatch"]
    assert ev and "ghost" in ev[0]["detail"]


def test_reconcile_flags_job_without_inflight_for_this_profile(rt):
    make_batch(rt.root, "orphan")
    queue.Queue(rt.root).add(make_job("orphan", prio=5))
    problems = reconcile.reconcile(rt)
    assert any("orphan" in p and "job_without_inflight" in p for p in problems)


def test_reconcile_ignores_other_profile_and_non_runtime_jobs(rt):
    make_batch(rt.root, "other")
    q = queue.Queue(rt.root)
    q.add(model.Job(store="other", sim_profile="other_p", profile_hash="h", prio=5, n=3))
    make_batch(rt.root, "smoke1", seed=3)
    q.add(model.Job(store="smoke1", sim_profile="fake_f1", profile_hash=testing.FAKE_PROFILE.profile_hash, prio=1, n=3,
                    origin="cli:smoke"))
    assert reconcile.reconcile(rt) == []


def test_reconcile_ignores_terminal_jobs(rt):
    make_batch(rt.root, "olddone")
    q = queue.Queue(rt.root)
    q.add(make_job("olddone"))
    q.pick("216")
    q.mark_done("olddone", "216", n_done=3, n_error=0, error_ids=[])
    assert reconcile.reconcile(rt) == []
    assert isinstance(rt, core.Runtime)


def test_reconcile_fail_with_inflight_and_abandoned_done_are_both_consistent(rt):
    """review-2：fail 不是終態——fail＋inflight＝正常（等接管）；abandon 後＝done＋無 inflight＝正常。"""
    make_batch(rt.root, "f1")
    q = queue.Queue(rt.root)
    q.add(make_job("f1"))
    fs.atomic_write_json(paths.inflight_file(rt.root, "fake_f1", "f1"),
                         {"store": "f1", "strategy": "blind", "tick": 1, "seed": 0, "kind": "sample", "prio": 9,
                          "ids": [], "items": {}, "collected": [], "at": "x"})
    q.pick("216")
    q.mark_fail("f1", "216", "dead")
    assert reconcile.reconcile(rt) == []
    fs.release(paths.inflight_file(rt.root, "fake_f1", "f1"))
    q.mark_done("f1", "abandon:ricky", n_done=0, n_error=0, error_ids=[])
    assert reconcile.reconcile(rt) == []


@pytest.mark.parametrize("missing", ["manifest", "patterns"])
def test_reconcile_flags_inflight_without_complete_batch(rt, missing):
    make_batch(rt.root, "half")
    queue.Queue(rt.root).add(make_job("half"))
    fs.atomic_write_json(paths.inflight_file(rt.root, "fake_f1", "half"),
                         {"store": "half", "strategy": "blind", "tick": 1, "seed": 0, "kind": "sample", "prio": 9,
                          "ids": [], "items": {}, "collected": [], "at": "x"})
    assert reconcile.reconcile(rt) == []
    (paths.batch_manifest if missing == "manifest" else paths.batch_patterns)(rt.root, "half").unlink()
    assert any("half" in p for p in reconcile.reconcile(rt))
