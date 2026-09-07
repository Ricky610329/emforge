# -*- coding: utf-8 -*-
"""tests/worker/test_loop.py — `emforge/worker/loop.py`：worker 主迴圈（STOP → pick → gate → run_batch → mark）。

防什麼：I-10（啟動印版本）、I-1（啟動清掃）、守門失敗只判那批不停機、STOP 在 job 之間生效。
"""
from emforge import fs, paths, queue, testing
from emforge.worker import loop
from tests.worker.conftest import make_batch, make_job

P = testing.FAKE_PROFILE


def _events(root, tag="216"):
    return [e["event"] for e in fs.read_jsonl(root / paths.worker_log(tag))]


def test_loop_sweeps_workdir_and_prints_worker_ver_on_start(root, capsys):
    testing.make_fake_root(root)
    junk = root / "work" / "leftover"
    junk.mkdir(parents=True)
    rc = loop.worker_loop(root, "216", once=True, work_root=root / "work", sleep=lambda s: None)
    assert rc == 0 and not junk.exists()
    out = capsys.readouterr().out
    assert "emforge=" in out and "216" in out
    assert _events(root) == ["worker_start", "worker_stop"]


def test_loop_once_returns_after_one_job(root):
    testing.make_fake_root(root)
    ids = make_batch(root, "s1")
    q = queue.Queue(root)
    q.add(make_job("s1"))
    rc = loop.worker_loop(root, "216", once=True, work_root=root / "work", sleep=lambda s: None)
    assert rc == 0 and q.state("s1") == "done"
    done = fs.read_json(root / paths.done_file("s1"))
    assert done["n_done"] == 3 and done["n_error"] == 0 and done["machine"] == "216"
    assert set((root / paths.batch_results_dir("s1")).glob("*.json")) and len(ids) == 3
    ev = _events(root)
    assert ev[:2] == ["worker_start", "job_claimed"] and "job_done" in ev and ev[-1] == "worker_stop"
    assert not list((root / "db").glob("*/*.npz")), "worker 不寫資料庫"


def test_loop_gate_failure_marks_fail_and_continues_to_next_job(root):
    testing.make_fake_root(root)
    make_batch(root, "bad")
    make_batch(root, "good", seed=1)
    q = queue.Queue(root)
    q.add(make_job("bad", prio=1, profile_hash="0" * 12))
    q.add(make_job("good", prio=5))
    rc = loop.worker_loop(root, "216", once=True, work_root=root / "work", sleep=lambda s: None)
    assert rc == 0
    assert q.state("bad") == "fail" and q.state("good") == "done"
    assert fs.read_json(root / paths.fail_file("bad"))["last"].startswith("profile_hash_mismatch")
    assert "gate_rejected" in _events(root)


def test_loop_stop_file_finishes_current_job_then_exits(root):
    testing.make_fake_root(root)
    make_batch(root, "s1")
    make_batch(root, "s2", seed=2)
    q = queue.Queue(root)
    q.add(make_job("s1", prio=1))
    q.add(make_job("s2", prio=5))

    class _StopSim(testing.FakeSimulator):
        def simulate(self, bits):
            q.request_stop("216")                # 跑第一筆時有人建了 STOP
            return super().simulate(bits)

    rc = loop.worker_loop(root, "216", once=False, work_root=root / "work", sleep=lambda s: None,
                          sim_factory=lambda wd, p: _StopSim(workdir=str(wd), profile=p))
    assert rc == 0
    assert q.state("s1") == "done", "當前 job 跑完"
    assert q.state("s2") == "queued", "STOP 在 job 之間生效，不撿下一個"
    assert _events(root)[-1] == "worker_stop"


def test_loop_polls_when_queue_empty_until_stop(root):
    testing.make_fake_root(root)
    q = queue.Queue(root)
    polls = []

    def sleep(s):
        polls.append(s)
        if len(polls) == 3:
            q.request_stop()

    rc = loop.worker_loop(root, "216", once=False, poll_s=30, work_root=root / "work", sleep=sleep)
    assert rc == 0 and polls == [30, 30, 30]


def test_loop_survives_filesystem_error_marks_fail_and_continues(root, monkeypatch):
    """回歸 review-6：結果檔寫不進去（FsBusy／PermissionError／NAS 斷）以前會殺掉整個 worker 行程、claim 留著 45 分沒人接。
    現在那批判死（.fail 記 worker_exception）、worker 繼續跑下一個 job。"""
    testing.make_fake_root(root)
    make_batch(root, "s1")
    make_batch(root, "s2", seed=2)
    q = queue.Queue(root)
    q.add(make_job("s1", prio=1))
    q.add(make_job("s2", prio=5))
    from emforge import batches as bmod
    real = bmod.Batch.write_result

    def flaky(self, rec_id, result):
        if self.store == "s1":
            raise fs.FsBusy("結果檔被別人開著")
        return real(self, rec_id, result)

    monkeypatch.setattr(bmod.Batch, "write_result", flaky)
    rc = loop.worker_loop(root, "216", once=False, work_root=root / "work",
                          sleep=lambda s: q.request_stop("216"))
    assert rc == 0
    assert q.state("s1") == "fail" and "worker_exception" in fs.read_json(root / paths.fail_file("s1"))["last"]
    assert not (root / paths.claim_file("s1")).exists(), "claim 不留著"
    assert q.state("s2") == "done", "worker 活著，下一個照跑"
    ev = _events(root)
    assert "job_failed" in ev and ev[-1] == "worker_stop"
    assert not (root / "work" / "s1").exists(), "工作目錄還是清掉"


def test_loop_run_batch_fail_marks_fail(root):
    testing.make_fake_root(root)
    ids = make_batch(root, "s1")
    q = queue.Queue(root)
    q.add(make_job("s1"))
    rc = loop.worker_loop(root, "216", once=True, work_root=root / "work", sleep=lambda s: None,
                          sim_factory=lambda wd, p: testing.FakeSimulator(workdir=str(wd), profile=p, fail_ids=set(ids)),
                          max_fail=1, max_blowout=1)
    assert rc == 0 and q.state("s1") == "fail"
    assert "216" in fs.read_json(root / paths.fail_file("s1"))["machines"]


# ── M13：worker 經儀器 ────────────────────────────────────────────────────────
def test_loop_does_not_pick_jobs_while_estop_engaged_and_resumes_after_clear(root):
    from emforge.device import estop
    testing.make_fake_root(root)
    make_batch(root, "s1")
    q = queue.Queue(root)
    q.add(make_job("s1"))
    estop.engage(q.depot, None, by="ricky", reason="全停")
    polls = []

    def sleep(s):
        polls.append(s)
        estop.clear(q.depot, None)

    rc = loop.worker_loop(root, "216", once=True, work_root=root / "work", sleep=sleep, poll_s=7)
    assert rc == 0 and q.state("s1") == "done" and polls == [7], "急停中不撿 job；解除後才跑"
    ev = [e["event"] for e in q.depot.read_log(paths.device_log("216"))]
    assert ev[0] == "device_start" and "estop_engaged" in ev and "estop_cleared" in ev and ev[-1] == "device_stop"
    assert q.depot.get_json(paths.device_state("216"))["state"] == "idle"


def test_loop_releases_claim_when_instrument_lease_is_held(root):
    from emforge.device.instrument import Instrument
    testing.make_fake_root(root)
    make_batch(root, "s1")
    q = queue.Queue(root)
    q.add(make_job("s1"))
    inst = Instrument(root, "216", depot=q.depot, work_root=root / "work", sleep=lambda s: None,
                      sim_factory=lambda wd, p: testing.FakeSimulator(workdir=str(wd), profile=p))
    inst.acquire("mcp:ricky")
    polls = []

    def sleep(s):
        polls.append(s)
        inst.release("mcp:ricky")

    rc = loop.worker_loop(root, "216", once=True, work_root=root / "work", sleep=sleep, instrument=inst)
    assert rc == 0 and q.state("s1") == "done" and len(polls) == 1
    log = fs.read_jsonl(root / paths.worker_log("216"))
    assert any(e["event"] == "job_yield" and e["reason"] == "device_busy" for e in log)
    assert inst.state.owner is None, "跑完放掉租約"


def test_loop_estop_between_pick_and_open_yields_and_same_machine_picks_again_after_clear(root, monkeypatch):
    """檢查 #6（repro_estop_fail）：急停在 pick 之後才按下 → 讓位、不進 .fail；解除後**同一台**能再撿。"""
    from emforge.device import estop
    testing.make_fake_root(root)
    make_batch(root, "s1")
    q = queue.Queue(root)
    q.add(make_job("s1"))

    class PickThenEstop(queue.Queue):
        def pick(self, tag, **kw):
            job = super().pick(tag, **kw)
            if job is not None:
                estop.engage(self.depot, None, by="ricky", reason="驗收")
            return job

    monkeypatch.setattr(loop, "Queue", PickThenEstop)
    rc = loop.worker_loop(root, "216", once=True, work_root=root / "work", sleep=lambda s: None)
    assert rc == 0 and q.state("s1") == "queued" and not (root / paths.fail_file("s1")).exists()
    log = fs.read_jsonl(root / paths.worker_log("216"))
    assert any(e["event"] == "job_yield" and e["reason"] == "estop_engaged" for e in log)
    assert not any(e["event"] == "job_failed" for e in log)
    estop.clear(q.depot, None)
    assert q.pick("216") is not None and q.claim_owner("s1") == "216", "解除後同一台再撿"
