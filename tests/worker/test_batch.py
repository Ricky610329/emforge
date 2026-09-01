# -*- coding: utf-8 -*-
"""tests/worker/test_batch.py — `emforge/worker/batch.py`：`run_batch` 逐筆迴圈。

防什麼：I-1（工作目錄）、I-10（版本戳）、I-12（可續跑）、看門狗、保險絲、讓位、worker 不碰量測。
"""
import inspect
import shutil
from pathlib import Path

import numpy as np

from emforge import fs, paths, testing
from emforge.worker import batch as wb
from emforge.worker.fuse import Fuse
from emforge.worker.workdir import WorkDir
from tests.worker.conftest import make_batch, make_job

P = testing.FAKE_PROFILE


def _factory(**kw):
    return lambda wd: testing.FakeSimulator(workdir=str(wd), profile=P, **kw)


def _run(root, claimed, factory=None, **kw):
    q, b, job, ids = claimed
    work = WorkDir(root / "work")
    events = []
    kw.setdefault("sleep", lambda s: None)
    out = wb.run_batch(q, b, job, P, factory or _factory(), "216", "emforge=test", work=work,
                       log=lambda name, **f: events.append((name, f)), **kw)
    return out, b.results(), events, work


def test_writes_result_file_per_sample_and_resumes_skipping_done(root, claimed):
    out, res, _, _ = _run(root, claimed)
    q, b, job, ids = claimed
    assert out == "done" and set(res) == set(ids) and all(r["status"] == "done" for r in res.values())
    r = res[ids[0]]
    assert np.asarray(r["response"], np.float32).shape == (2, 17) and r["attempts"] == 1 and r["extra"] == {}
    paths.batch_result(root, job.store, ids[1]).unlink()      # 模擬中斷：少一筆
    q.pick("216")                                             # 續跑自己的 claim
    sims = []

    def factory(wd):
        s = testing.FakeSimulator(workdir=str(wd), profile=P)
        sims.append(s)
        return s

    out2, res2, _, _ = _run(root, claimed, factory)
    assert out2 == "done" and set(res2) == set(ids) and sims[0].calls["simulate"] == 1, "只補那一筆"


def test_records_error_and_continues(root, claimed):
    q, b, job, ids = claimed
    out, res, events, _ = _run(root, claimed, _factory(fail_ids={ids[1]}), retry_passes=0)
    assert out == "done"
    assert res[ids[1]]["status"] == "error" and "fake failure" in res[ids[1]]["error"] and res[ids[1]]["attempts"] == 1
    assert res[ids[0]]["status"] == res[ids[2]]["status"] == "done"
    assert [e for e, _ in events].count("sample_error") == 1 and [e for e, _ in events].count("sample_done") == 2


def test_retry_pass_retries_errors_until_attempts_3(root, claimed):
    q, b, job, ids = claimed
    out, res, events, _ = _run(root, claimed, _factory(fail_ids={ids[1]}), retry_passes=2)
    assert out == "done" and res[ids[1]]["status"] == "error" and res[ids[1]]["attempts"] == 3
    assert [e for e, _ in events].count("sim_restart") == 2, "每輪補測前殺透重開"
    assert res[ids[0]]["attempts"] == 1, "成功的不重跑"


def test_fuse_blowout_returns_fail(root, claimed):
    q, b, job, ids = claimed
    slept = []
    out, res, events, _ = _run(root, claimed, _factory(fail_ids=set(ids)), retry_passes=0,
                               fuse=Fuse(max_fail=1, cooldown_s=7, max_blowout=2), sleep=slept.append)
    assert out == "fail"
    assert slept == [7], "第一次熔斷冷卻一次；第二次直接判死"
    assert [e for e, _ in events].count("sim_restart") == 1
    assert ("job_failed", {"store": job.store, "reason": "fuse_blown"}) in events


def test_watchdog_timeout_tags_watchdog_timeout(root, claimed):
    q, b, job, ids = claimed
    sims = []

    def factory(wd):
        s = testing.FakeSimulator(workdir=str(wd), profile=P, hang_ids={ids[0]})
        sims.append(s)
        return s

    out, res, events, _ = _run(root, claimed, factory, timeout_s=0.3, retry_passes=0)
    assert out == "done"
    assert res[ids[0]]["status"] == "error" and res[ids[0]]["error"].startswith("watchdog_timeout")
    assert sims[0].calls["kill"] >= 1 and ("sim_restart", {"store": job.store, "reason": "watchdog_timeout"}) in events
    assert res[ids[1]]["status"] == "done"


def test_result_stamps_profile_hash_worker_ver_machine_time_s(root, claimed):
    """I-10：每筆結果自帶「誰、哪個版本、哪個儀器指紋量的」。"""
    q, b, job, ids = claimed
    _, res, _, _ = _run(root, claimed)
    r = res[ids[0]]
    assert r["profile_hash"] == P.profile_hash and r["worker_ver"] == "emforge=test" and r["machine"] == "216"
    assert isinstance(r["time_s"], float) and len(r["at"]) == 19 and r["id"] == ids[0]


def test_workdir_removed_on_done_fail_yield(root, claimed):
    """回歸 I-1（2026-07-15）：三種結束都刪工作目錄。"""
    q, b, job, ids = claimed
    seen = []

    def factory(wd):
        seen.append(Path(wd))
        assert Path(wd).is_dir()
        return testing.FakeSimulator(workdir=str(wd), profile=P)

    out, _, _, work = _run(root, claimed, factory)
    assert out == "done" and not seen[0].exists()
    q.mark_done(job.store, "216", n_done=3, n_error=0, error_ids=[])
    q.requeue(job.store)
    shutil.rmtree(paths.batch_results_dir(root, job.store))   # requeue 保留進度；要重跑得清結果
    q.pick("216")
    out, _, _, work = _run(root, claimed, _factory(fail_ids=set(ids)), fuse=Fuse(max_fail=1, cooldown_s=0, max_blowout=1),
                           retry_passes=0)
    assert out == "fail" and not work.path(job.store).exists()


class _HookSim(testing.FakeSimulator):
    hook = None

    def simulate(self, bits):
        r = super().simulate(bits)
        if _HookSim.hook:
            _HookSim.hook()
        return r


def test_yields_when_claim_taken_over(root, claimed):
    q, b, job, ids = claimed
    calls = {"n": 0}

    def steal():
        calls["n"] += 1
        if calls["n"] == 1:
            fs.release(paths.claim_file(root, job.store))
            fs.try_claim(paths.claim_file(root, job.store), {"machine": "218", "at": "x"})

    _HookSim.hook = steal
    try:
        out, res, events, work = _run(root, claimed, lambda wd: _HookSim(workdir=str(wd), profile=P))
    finally:
        _HookSim.hook = None
    assert out == "yield" and len(res) == 1, "第一筆寫完就發現 claim 被搶 → 停寫退出"
    assert q.claim_owner(job.store) == "218", "別人的 claim 不動"
    assert ("job_yield", {"store": job.store, "reason": "claim_taken_over"}) in events
    assert not work.path(job.store).exists()


def test_background_job_yields_when_foreground_appears(root, claimed):
    q, b, job, ids = claimed
    job.prio = 9
    calls = {"n": 0}

    def add_fg():
        calls["n"] += 1
        if calls["n"] == 1:
            make_batch(root, "fg", n=1, seed=9)
            q.add(make_job("fg", prio=3))

    _HookSim.hook = add_fg
    try:
        out, res, events, _ = _run(root, claimed, lambda wd: _HookSim(workdir=str(wd), profile=P), background_prio=9)
    finally:
        _HookSim.hook = None
    assert out == "yield" and len(res) == 1
    assert q.claim_owner(job.store) is None, "讓位＝釋放 claim，空窗時任一機續跑"
    assert ("job_yield", {"store": job.store, "reason": "foreground_job_appeared"}) in events
    assert q.pick("216").store == "fg", "前景先跑"


def test_worker_knows_no_measure_or_score(root, claimed):
    """worker 只寫原始響應；measure／score 是 runtime collect 的事（領域無關）。"""
    _, res, _, _ = _run(root, claimed)
    for r in res.values():
        assert "measure" not in r and "score" not in r
    src = inspect.getsource(wb)
    assert "specs" not in src and "measure(" not in src
