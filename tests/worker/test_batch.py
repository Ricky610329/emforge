# -*- coding: utf-8 -*-
"""tests/worker/test_batch.py — `emforge/worker/batch.py`：`run_batch` 逐筆迴圈。

防什麼：I-1（工作目錄）、I-10（版本戳）、I-12（可續跑）、看門狗、保險絲、讓位、worker 不碰量測。
"""
import inspect
import shutil
import time
from pathlib import Path

import numpy as np

from emforge import paths, testing
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
    (root / paths.batch_result(job.store, ids[1])).unlink()      # 模擬中斷：少一筆
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
    shutil.rmtree(root / paths.batch_results_dir(job.store))   # requeue 保留進度；要重跑得清結果
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
            q.depot.release(paths.claim_file(job.store))
            q.depot.claim(paths.claim_file(job.store), {"owner": "218", "at": "x"})

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


def test_pass0_skips_poison_samples_with_attempts_at_max(root, claimed):
    """回歸 review-10：批被讓位／接管／requeue 重進時，第 0 輪以前不看 attempts，毒樣本第 4、5、6 次再跑、每台都吃一次逾時。
    現在第 0 輪也套 attempts < 3。"""
    q, b, job, ids = claimed
    b.write_result(ids[0], {"id": ids[0], "status": "error", "error": "watchdog_timeout: 毒", "attempts": 3,
                            "machine": "218", "worker_ver": "v", "profile_hash": P.profile_hash, "at": "t"})
    sims = []

    def factory(wd):
        s = testing.FakeSimulator(workdir=str(wd), profile=P)
        sims.append(s)
        return s

    out, res, _, _ = _run(root, claimed, factory)
    assert out == "done"
    assert res[ids[0]]["attempts"] == 3 and res[ids[0]]["status"] == "error", "毒樣本不再跑"
    assert sims[0].calls["simulate"] == 2 and all(res[i]["status"] == "done" for i in ids[1:])


def test_run_batch_touches_claim_after_each_sample(root, claimed):
    """review（砍掉的 queue.py:165）配套：worker 每筆後 touch claim，claim mtime 才是真的心跳。"""
    q, b, job, ids = claimed
    q.depot.set_modified_at(paths.claim_file(job.store), time.time() - 3600)
    _run(root, claimed)
    assert time.time() - q.depot.modified_at(paths.claim_file(job.store)) < 5


def test_worker_knows_no_measure_or_score(root, claimed):
    """worker 只寫原始響應；measure／score 是 runtime collect 的事（領域無關）。"""
    _, res, _, _ = _run(root, claimed)
    for r in res.values():
        assert "measure" not in r and "score" not in r
    src = inspect.getsource(wb)
    assert "specs" not in src and "measure(" not in src


# ── M13：經儀器跑批 ──────────────────────────────────────────────────────────
def _inst(root, q, factory=None, **kw):
    from emforge.device.instrument import Instrument
    kw.setdefault("sleep", lambda s: None)
    return Instrument(root, "216", depot=q.depot, work_root=root / "work", worker_ver="emforge=test",
                      sim_factory=factory or (lambda wd, p: testing.FakeSimulator(workdir=str(wd), profile=p)), **kw)


def test_run_batch_via_instrument_done_and_instrument_back_to_idle(root, claimed):
    q, b, job, ids = claimed
    inst = _inst(root, q)
    inst.start()
    out, res, events, work = _run(root, claimed, instrument=inst)
    assert out == "done" and len(res) == 3 and all(r["status"] == "done" for r in res.values())
    assert inst.state.state == "idle" and inst.state.n_done == 3 and inst.sim is None and inst._bound is None
    assert q.depot.get_json(paths.device_state("216"))["n_done"] == 3
    inst.stop()


def test_run_batch_via_instrument_yields_on_estop_before_next_sample(root, claimed):
    from emforge.device import estop
    q, b, job, ids = claimed
    inst = _inst(root, q, factory=lambda wd, p: _HookSim(workdir=str(wd), profile=p))
    inst.start()
    calls = {"n": 0}

    def engage():
        calls["n"] += 1
        if calls["n"] == 1:
            estop.engage(q.depot, "216", by="ricky", reason="冒煙")

    _HookSim.hook = engage
    try:
        out, res, events, work = _run(root, claimed, instrument=inst)
    finally:
        _HookSim.hook = None
    assert out == "yield" and len(res) == 1, "第一筆寫完、下一筆前看到急停 → 讓位"
    assert ("job_yield", {"store": job.store, "reason": "estop_engaged"}) in events
    assert q.claim_owner(job.store) is None, "放掉 claim：急停可能只停這台，別台可續跑"
    assert inst.state.state == "estop" and not work.path(job.store).exists()
    inst.stop()


def test_run_batch_aborts_running_sample_on_estop_and_counts_attempt(root, claimed):
    from emforge.device import estop
    q, b, job, ids = claimed

    class _HangThenEstop(testing.FakeSimulator):
        """第一筆開始跑之後才按急停（不能用計時器：open 前的體檢時間不定，急停可能在第一筆前就被看到）。"""

        def simulate(self, bits):
            if testing.record_id(bits, self.profile.name) in self.hang_ids:
                estop.engage(q.depot, None, by="x", reason="r")
            return super().simulate(bits)

    inst = _inst(root, q, factory=lambda wd, p: _HangThenEstop(workdir=str(wd), profile=p, hang_ids={ids[0]}))
    inst.start()
    out, res, events, _ = _run(root, claimed, instrument=inst, timeout_s=10, retry_passes=0, estop_poll_s=0.02)
    assert out == "yield"
    r = res[ids[0]]
    assert r["status"] == "error" and r["error"].startswith("aborted: estop_engaged") and r["attempts"] == 1, "吃一次 attempts"
    assert "sim_restart" not in [e for e, _ in events], "急停中止不重開模擬器"
    assert ("job_yield", {"store": job.store, "reason": "estop_engaged"}) in events
    inst.stop()


# ── 檢查 #6（2026-09-07）：急停在 open 之前是「拒絕」不是「卡住」 ──────────────
def test_run_batch_via_instrument_yields_when_estop_engaged_before_open(root, claimed):
    """repro_estop_fail：急停落在 pick 之後、open 之前——Instrument.open() 拋 EstopEngaged 不能被當成機器卡住重試三次、
    寫進 .fail（這台從此不撿這批）；要讓位：job_yield reason=estop_engaged、放掉自己的 claim、不記 job_failed。"""
    from emforge.device import estop
    q, b, job, ids = claimed
    inst = _inst(root, q)
    inst.start()
    estop.engage(q.depot, None, by="ricky", reason="驗收")
    sleeps = []
    out, res, events, work = _run(root, claimed, instrument=inst, sleep=lambda s: sleeps.append(s))
    names = [e for e, _ in events]
    assert out == "yield" and res == {} and "job_failed" not in names and "sim_restart" not in names
    assert ("job_yield", {"store": job.store, "reason": "estop_engaged"}) in events
    assert q.claim_owner(job.store) is None and q.state(job.store) == "queued"
    assert 15.0 not in sleeps, "不走三試的 retry_wait"
    assert inst.sim is None and inst._bound is None and not work.path(job.store).exists()
    inst.stop()


def test_run_batch_via_instrument_precondition_failed_is_fail_without_retries(root, claimed):
    """前置檢查不過（真的不能開）維持判死，但同樣不重試三次、原因指名 precondition_failed。"""
    from emforge.device.limits import Limits
    q, b, job, ids = claimed
    inst = _inst(root, q, limits=Limits(allowed_profiles=("nope",)))
    inst.start()
    sleeps = []
    out, res, events, work = _run(root, claimed, instrument=inst, sleep=lambda s: sleeps.append(s))
    failed = [f for e, f in events if e == "job_failed"]
    assert out == "fail" and res == {} and failed and failed[0]["reason"].startswith("precondition_failed")
    assert 15.0 not in sleeps and "sim_restart" not in [e for e, _ in events]
    inst.stop()


def test_restart_via_instrument_kills_before_close_so_kill_reaches_the_sim(root, claimed):
    """檢查 #17：_restart 以前 close→kill——Instrument.close() 先把 sim 設 None，kill 打空（殺不透，殘留 ansysedt 累積）。
    現在 kill→close→open，殺透那一下打在舊模擬器上。"""
    q, b, job, ids = claimed
    sims = []

    def factory(wd, p):
        s = testing.FakeSimulator(workdir=str(wd), profile=p, hang_ids={ids[0]} if not sims else ())
        sims.append(s)
        return s

    inst = _inst(root, q, factory=factory)
    inst.start()
    out, res, events, _ = _run(root, claimed, instrument=inst, timeout_s=0.3, retry_passes=0)
    inst.stop()
    assert out == "done" and res[ids[0]]["error"].startswith("watchdog_timeout")
    assert ("sim_restart", {"store": job.store, "reason": "watchdog_timeout"}) in events
    assert len(sims) == 2, "重開＝新的模擬器"
    assert sims[0].calls["kill"] == 2 and sims[0].calls["close"] == 1, "看門狗一次＋重開前殺透一次，都打在舊模擬器上"


def test_close_quiet_has_a_watchdog_that_kills_a_hung_close(root, monkeypatch):
    """檢查 #18：COM 的 quit() 可以無例外地永遠不回來——以前 _close_quiet 只有 try/except，卡住抓不到。"""
    from emforge.worker import guard
    monkeypatch.setattr(guard, "CLOSE_TIMEOUT_S", 0.3)

    class _HangClose(testing.FakeSimulator):
        def close(self):
            self.calls["close"] += 1
            self._killed.wait(5)

    sim = _HangClose(workdir=str(root / "w"), profile=P)
    t0 = time.time()
    wb._close_quiet(sim)
    assert time.time() - t0 < 3 and sim.calls["kill"] == 1 and sim.calls["close"] == 1
