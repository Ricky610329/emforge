# -*- coding: utf-8 -*-
"""tests/test_cli.py — `emforge/cli.py`：子命令 ↔ cmd_ 對帳、exit code 契約、每個命令行程內跑一遍。

回歸 I-8（2026-08-06）：安全閘退出碼被吃掉靜默失效——每個命令都回 int、main 不吞例外、失敗一律非零。
"""
import os
import time

import numpy as np
import pytest

from emforge import batches, cli, doctor, fs, ledger, model, paths, queue, testing
from emforge.runtime.core import Runtime
from tests.runtime.conftest import make_rt, write_yaml
from tests.worker.conftest import make_batch, make_job

P = testing.FAKE_PROFILE


def _main(*argv):
    return cli.main([str(a) for a in argv])


@pytest.fixture
def fake(root):
    testing.make_fake_root(root)
    write_yaml(root)
    return root


# ── 結構 ────────────────────────────────────────────────────────────────────
def test_every_subcommand_kebab_maps_to_cmd_snake():
    parser = cli.build_parser()
    subs = parser._subparsers._group_actions[0].choices
    assert set(subs) == set(cli.COMMANDS)
    for name, sp in subs.items():
        fn = sp.get_default("fn")
        assert fn is not None and fn.__name__ == "cmd_" + name.replace("-", "_"), name
        assert name == name.lower() and "_" not in name, "CLI 子命令用 kebab-case"
    for must in ("init", "run", "worker", "doctor", "status", "events", "pending", "jobs", "watch", "report", "promote",
                 "retire", "rescore", "requeue", "resume", "stop", "check-strategy", "smoke", "version"):
        assert must in subs


def test_main_converts_exceptions_to_exit_1_with_stderr(root, capsys):
    rc = _main("promote", "0" * 16, "--root", root, "--profile", "nonexistent_p", "--by", "x")
    assert rc == 1
    assert "emforge:" in capsys.readouterr().err


# ── init / version / check-strategy ─────────────────────────────────────────
def test_init_creates_root_layout_and_templates(root, capsys):
    assert _main("init", "--root", root, "--profile", "fake_f1") == 0
    for d in paths.layout_dirs(root):
        assert d.is_dir()
    assert paths.registry_py(root).exists() and "register" in paths.registry_py(root).read_text(encoding="utf-8")
    y = paths.strategies_yaml(root, "fake_f1").read_text(encoding="utf-8")
    assert "profile: fake_f1" in y and "blind" in y
    assert _main("init", "--root", root, "--profile", "fake_f1") == 0, "重跑不覆寫既有檔"
    assert "已存在" in capsys.readouterr().out


def test_version_prints_worker_ver(capsys):
    assert _main("version") == 0
    assert "emforge=" in capsys.readouterr().out


def test_check_strategy_dry_runs_in_process(fake, capsys):
    assert _main("check-strategy", "--root", fake, "--profile", "fake_f1", "--strategy", "blind", "--budget", 3) == 0
    out = capsys.readouterr().out
    assert "3" in out and "blind" in out
    assert _main("check-strategy", "--root", fake, "--profile", "fake_f1", "--strategy", "nope") == 1


# ── run / worker ────────────────────────────────────────────────────────────
def test_run_once_single_tick(fake):
    assert _main("run", "--root", fake, "--profile", "fake_f1", "--once", "--in-process") == 0
    assert fs.read_json(paths.status_json(fake, "fake_f1"))["tick"] == 1


def test_run_reconcile_mismatch_returns_2(fake):
    fs.atomic_write_json(paths.inflight_file(fake, "fake_f1", "ghost"),
                         {"store": "ghost", "strategy": "blind", "tick": 1, "seed": 0, "kind": "sample", "prio": 9,
                          "ids": [], "items": {}, "collected": [], "at": "x"})
    assert _main("run", "--root", fake, "--profile", "fake_f1", "--once", "--in-process") == 2


def test_run_locked_returns_3(fake):
    rt = make_rt(fake)
    rt.acquire_lock()
    try:
        assert _main("run", "--root", fake, "--profile", "fake_f1", "--once", "--in-process") == 3
    finally:
        rt.release_lock()


def test_worker_once_runs_job(fake):
    make_batch(fake, "s1")
    queue.Queue(fake).add(make_job("s1"))
    assert _main("worker", "--root", fake, "--machine-tag", "216", "--once", "--work-root", fake / "work") == 0
    assert queue.Queue(fake).state("s1") == "done"


# ── 讀 ───────────────────────────────────────────────────────────────────────
def test_status_events_pending_jobs_print(fake, capsys):
    assert _main("status", "--root", fake, "--profile", "fake_f1") == 0
    assert "尚無" in capsys.readouterr().out
    _main("run", "--root", fake, "--profile", "fake_f1", "--once", "--in-process")
    assert _main("status", "--root", fake, "--profile", "fake_f1") == 0
    assert '"tick": 1' in capsys.readouterr().out
    assert _main("events", "--root", fake, "--profile", "fake_f1", "--last", 5) == 0
    out = capsys.readouterr().out
    assert "runtime_start" in out or "runtime_stop" in out
    assert _main("events", "--root", fake, "--profile", "fake_f1", "--event", "batch_dispatched") == 0
    assert "batch_dispatched" in capsys.readouterr().out
    assert _main("pending", "--root", fake, "--profile", "fake_f1") == 0
    assert _main("jobs", "--root", fake) == 0
    assert "blind" in capsys.readouterr().out


def test_watch_exit_codes_0_1_2(fake):
    make_batch(fake, "a")
    make_batch(fake, "b", seed=2)
    q = queue.Queue(fake)
    q.add(make_job("a"))
    q.add(make_job("b"))
    q.pick("216")
    q.mark_done("a", "216", n_done=3, n_error=0, error_ids=[])
    q.pick("216")
    q.mark_done("b", "216", n_done=3, n_error=0, error_ids=[])
    assert q.watch(["a", "b"], poll_s=0, sleep=lambda s: None) == 0
    make_batch(fake, "c", seed=3)
    q.add(make_job("c"))
    q.pick("216")
    q.mark_fail("c", "216", "dead")
    assert q.watch(["a", "c"], poll_s=0, fail_grace_s=0, sleep=lambda s: None) == 1
    make_batch(fake, "d", seed=4)
    q.add(make_job("d"))
    assert q.watch(["d"], poll_s=1, timeout_s=0, sleep=lambda s: None) == 2
    assert _main("watch", "--root", fake, "--stores", "a,b", "--poll-s", 0) == 0
    assert _main("watch", "--root", fake, "--stores", "d", "--poll-s", 0, "--timeout-min", 0) == 2


def test_report_cross_profile_without_flag_returns_2(fake, capsys):
    assert _main("report", "--root", fake, "--profile", "fake_f1", "--profile", "other_p") == 2
    assert _main("report", "--root", fake, "--profile", "fake_f1", "--profile", "other_p", "--cross-profile") == 0
    assert "跨 profile" in capsys.readouterr().out
    assert _main("report", "--root", fake, "--profile", "fake_f1") == 0


# ── 榜 ───────────────────────────────────────────────────────────────────────
def _seed_record(root, seed, score):
    from emforge import db as dbm
    b = np.random.default_rng(seed).random(P.shape) > 0.5
    b[P.fixed_on] = True
    rec = model.Record(id=model.record_id(b, P.name), sim_profile=P.name, bits=b, response=np.zeros((2, 17), np.float32),
                       measure={"m1": score, "m2": score, "m3": score, "m4": score}, score=score, status="done",
                       strategy="blind", arm="blind", parent=None, tick=1, seed=seed, note={}, kind="sample",
                       run={"store": "s", "machine": "216", "worker_ver": "v", "profile_hash": P.profile_hash, "time_s": 1.0})
    dbm.Database(root, write_profile=P.name).add(rec)
    return rec


def test_promote_unknown_id_nonzero_prints_reason(fake, capsys):
    """回歸 I-8：安全閘失敗必須是非零 exit code＋stderr 原因。"""
    assert _main("promote", "f" * 16, "--root", fake, "--profile", "fake_f1", "--by", "ricky") == 1
    assert "不在 db" in capsys.readouterr().err
    rec = _seed_record(fake, 1, -2.0)
    assert _main("promote", rec.id, "--root", fake, "--profile", "fake_f1", "--by", "ricky") == 3
    assert "pending" in capsys.readouterr().err
    assert _main("promote", rec.id, "--root", fake, "--profile", "fake_f1", "--by", "ricky", "--force", "--note", "人工") == 0
    assert ledger.Ledger(fake, "fake_f1", "fake_v1").best()["id"] == rec.id
    ev = [e for e in fs.read_jsonl(paths.events_jsonl(fake, "fake_f1")) if e["event"] == "promoted"]
    assert ev and ev[0]["by"] == "ricky"
    assert _main("promote", rec.id, "--root", fake, "--profile", "fake_f1", "--by", "") == 1


def test_rescore_and_retire(fake, capsys):
    _seed_record(fake, 1, -2.0)
    _seed_record(fake, 2, -1.0)
    assert _main("rescore", "--root", fake, "--profile", "fake_f1", "--spec", "fake_v1", "--by", "ricky") == 0
    assert "n=2" in capsys.readouterr().out
    assert _main("rescore", "--root", fake, "--profile", "fake_f1", "--spec", "fake_v1", "--by", "ricky") == 6
    assert _main("rescore", "--root", fake, "--profile", "fake_f1", "--spec", "fake_v1", "--by", "ricky", "--force") == 0
    assert _main("rescore", "--root", fake, "--profile", "fake_f1", "--spec", "unregistered_v9", "--by", "ricky") == 1
    assert _main("retire", "--root", fake, "--profile", "fake_f1", "--by", "ricky") == 0
    assert paths.retired_marker(fake, "fake_f1").exists()
    assert [e["event"] for e in fs.read_jsonl(paths.events_jsonl(fake, "fake_f1"))][-1] == "retired"


def test_requeue_refuses_live_claim(fake):
    make_batch(fake, "s1")
    q = queue.Queue(fake)
    q.add(make_job("s1"))
    q.pick("216")
    assert _main("requeue", "s1", "--root", fake, "--by", "ricky") == 7
    old = time.time() - 3 * 3600
    os.utime(paths.claim_file(fake, "s1"), (old, old))
    assert _main("requeue", "s1", "--root", fake, "--by", "ricky") == 0
    assert q.state("s1") == "queued"
    assert _main("requeue", "nope", "--root", fake, "--by", "ricky") == 1


# ── 控制 ────────────────────────────────────────────────────────────────────
def test_stop_resume_profile_and_worker(fake):
    assert _main("stop", "--root", fake, "--profile", "fake_f1") == 0
    assert paths.runtime_stop(fake, "fake_f1").exists()
    assert _main("stop", "--root", fake, "--profile", "fake_f1", "--clear") == 0
    assert not paths.runtime_stop(fake, "fake_f1").exists()
    assert _main("stop", "--root", fake, "--worker", "--machine-tag", "216") == 0
    assert queue.Queue(fake).stop_requested("216") and not queue.Queue(fake).stop_requested("218")
    assert _main("stop", "--root", fake, "--worker") == 0 and queue.Queue(fake).stop_requested("218")
    assert _main("stop", "--root", fake, "--worker", "--clear") == 0
    assert _main("stop", "--root", fake, "--worker", "--machine-tag", "216", "--clear") == 0
    assert not queue.Queue(fake).stop_requested("216")
    # resume：寫 control.json，跑著的 runtime 下個 tick 消費（不直接改 state.json——runtime 每 tick 會覆寫）
    rt = make_rt(fake)
    rt.strategy_state("blind")["paused"] = True
    rt.strategy_state("blind")["errors_consecutive"] = 3
    rt.state["paused_profile"] = {"store": "x", "error_rate": 1.0}
    rt.save_state()
    assert _main("resume", "--root", fake, "--profile", "fake_f1", "--strategy", "blind", "--by", "ricky") == 0
    assert _main("resume", "--root", fake, "--profile", "fake_f1", "--by", "ricky") == 0
    assert paths.control_json(fake, "fake_f1").exists()
    rt.apply_control()
    assert rt.strategy_state("blind")["paused"] is False and rt.strategy_state("blind")["errors_consecutive"] == 0
    assert rt.state["paused_profile"] is None and not paths.control_json(fake, "fake_f1").exists()
    ev = [e["event"] for e in fs.read_jsonl(paths.events_jsonl(fake, "fake_f1"))]
    assert "strategy_resumed" in ev and "profile_resumed" in ev


def test_smoke_dispatches_repeat_for_known_id(fake, capsys):
    rec = _seed_record(fake, 7, -1.0)
    assert _main("smoke", rec.id, "--root", fake, "--profile", "fake_f1", "--machine", "216", "--by", "ricky", "--n", 2) == 0
    out = capsys.readouterr().out
    jobs = queue.Queue(fake).list()
    assert len(jobs) == 2 and all(j.machine == "216" and j.prio == 1 and j.origin == "cli:smoke" for j in jobs)
    for j in jobs:
        inf = fs.read_json(paths.inflight_file(fake, "fake_f1", j.store))
        assert inf["kind"] == "repeat" and inf["strategy"] == "cli:smoke" and inf["ids"] == [rec.id]
        assert j.store in out and "smoke" in j.store and ":" not in j.store
    assert batches.Batch(fake, jobs[0].store).ids() == [rec.id]
    assert _main("smoke", "f" * 16, "--root", fake, "--profile", "fake_f1", "--machine", "216", "--by", "ricky") == 1


# ── doctor ──────────────────────────────────────────────────────────────────
def test_doctor_reports_versions_and_refuses_when_ansysedt_running(fake, capsys, monkeypatch):
    monkeypatch.setattr(doctor, "ansysedt_running", lambda: False)
    assert _main("doctor", "--root", fake) == 0
    out = capsys.readouterr().out
    assert "emforge=" in out and "python" in out.lower() and str(fake) in out and "可寫" in out
    monkeypatch.setattr(doctor, "ansysedt_running", lambda: True)
    assert _main("doctor", "--root", fake) == 4
    assert "ansysedt" in capsys.readouterr().out


def test_runtime_applies_control_at_tick_start(fake):
    rt = make_rt(fake)
    rt.strategy_state("blind")["paused"] = True
    fs.atomic_write_json(paths.control_json(fake, "fake_f1"), {"resume_strategies": ["blind"], "by": "ricky"})
    rt.acquire_lock()
    rt.tick()
    rt.release_lock()
    assert rt.strategy_state("blind")["paused"] is False
    assert isinstance(rt, Runtime)
