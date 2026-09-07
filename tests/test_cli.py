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
    for prefix in paths.layout_prefixes():
        assert (root / prefix).is_dir()
    assert paths.user_strategies_dir(root).is_dir()
    assert paths.registry_py(root).exists() and "register" in paths.registry_py(root).read_text(encoding="utf-8")
    y = (root / paths.strategies_yaml("fake_f1")).read_text(encoding="utf-8")
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
    assert fs.read_json(fake / paths.status_json("fake_f1"))["tick"] == 1


def test_run_reconcile_mismatch_returns_2(fake):
    fs.atomic_write_json(fake / paths.inflight_file("fake_f1", "ghost"),
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
    ev = [e for e in fs.read_jsonl(fake / paths.events_jsonl("fake_f1")) if e["event"] == "promoted"]
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
    assert (fake / paths.retired_marker("fake_f1")).exists()
    assert [e["event"] for e in fs.read_jsonl(fake / paths.events_jsonl("fake_f1"))][-1] == "retired"


def test_requeue_refuses_live_claim(fake):
    make_batch(fake, "s1")
    q = queue.Queue(fake)
    q.add(make_job("s1"))
    q.pick("216")
    assert _main("requeue", "s1", "--root", fake, "--by", "ricky") == 7
    old = time.time() - 3 * 3600
    os.utime(fake / paths.claim_file("s1"), (old, old))
    assert _main("requeue", "s1", "--root", fake, "--by", "ricky") == 0
    assert q.state("s1") == "queued"
    assert _main("requeue", "nope", "--root", fake, "--by", "ricky") == 1


# ── 控制 ────────────────────────────────────────────────────────────────────
def test_stop_resume_profile_and_worker(fake):
    assert _main("stop", "--root", fake, "--profile", "fake_f1") == 0
    assert (fake / paths.runtime_stop("fake_f1")).exists()
    assert _main("stop", "--root", fake, "--profile", "fake_f1", "--clear") == 0
    assert not (fake / paths.runtime_stop("fake_f1")).exists()
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
    assert (fake / paths.control_json("fake_f1")).exists()
    rt.apply_control()
    assert rt.strategy_state("blind")["paused"] is False and rt.strategy_state("blind")["errors_consecutive"] == 0
    assert rt.state["paused_profile"] is None and not (fake / paths.control_json("fake_f1")).exists()
    ev = [e["event"] for e in fs.read_jsonl(fake / paths.events_jsonl("fake_f1"))]
    assert "strategy_resumed" in ev and "profile_resumed" in ev


def test_smoke_cli_does_not_touch_state_json(fake):
    """回歸 review-4：smoke 建的第二個 Runtime 沒拿鎖，不准用舊快照覆寫跑著的 runtime 的 state.json。"""
    rec = _seed_record(fake, 7, -1.0)
    rt = make_rt(fake)
    rt.state["tick"] = 120
    rt.state["notarize"] = {"deadbeef": {"stores": ["x"], "tick": 118, "score": -1.0}}
    rt.save_state()
    p = fake / paths.state_json("fake_f1")
    before = (p.read_bytes(), p.stat().st_mtime_ns)
    assert _main("smoke", rec.id, "--root", fake, "--profile", "fake_f1", "--by", "ricky") == 0
    assert (p.read_bytes(), p.stat().st_mtime_ns) == before
    assert fs.read_json(p)["notarize"] == {"deadbeef": {"stores": ["x"], "tick": 118, "score": -1.0}}


def test_smoke_dispatches_repeat_for_known_id(fake, capsys):
    rec = _seed_record(fake, 7, -1.0)
    assert _main("smoke", rec.id, "--root", fake, "--profile", "fake_f1", "--machine", "216", "--by", "ricky", "--n", 2) == 0
    out = capsys.readouterr().out
    jobs = queue.Queue(fake).list()
    assert len(jobs) == 2 and all(j.machine == "216" and j.prio == 1 and j.origin == "cli:smoke" for j in jobs)
    for j in jobs:
        inf = fs.read_json(fake / paths.inflight_file("fake_f1", j.store))
        assert inf["kind"] == "repeat" and inf["strategy"] == "cli:smoke" and inf["ids"] == [rec.id]
        assert j.store in out and "smoke" in j.store and ":" not in j.store
    assert batches.Batch(fake, jobs[0].store).ids() == [rec.id]
    assert _main("smoke", "f" * 16, "--root", fake, "--profile", "fake_f1", "--machine", "216", "--by", "ricky") == 1


def test_abandon_cli(fake, capsys):
    """review-2：`emforge abandon <store>`——人宣告放棄沒人接管的 fail 批。"""
    rt = make_rt(fake)
    rt.acquire_lock()
    rt.tick()
    rt.release_lock()
    store = rt.inflight()[0]["store"]
    q = queue.Queue(fake)
    q.pick("216")
    q.mark_fail(store, "216", "dead")
    assert _main("abandon", store, "--root", fake, "--profile", "fake_f1", "--by", "ricky") == 0
    assert "abandoned" in capsys.readouterr().out
    assert not (fake / paths.inflight_file("fake_f1", store)).exists() and q.state(store) == "done"
    assert _main("abandon", store, "--root", fake, "--profile", "fake_f1", "--by", "ricky") == 1


# ── depot（M12d） ───────────────────────────────────────────────────────────
def test_depot_flag_and_env_select_backend(fake, monkeypatch):
    """M12d：`--depot` > `EMFORGE_DEPOT` > `FileDepot(root)`；`--root` 仍是本機程式碼／設定根。"""
    import argparse
    from emforge.cli.base import depot_of
    from emforge.depot import FileDepot, MemoryDepot, open_depot
    monkeypatch.delenv("EMFORGE_DEPOT", raising=False)
    d = depot_of(argparse.Namespace(depot=None), fake)
    assert isinstance(d, FileDepot) and d.root == fake
    monkeypatch.setenv("EMFORGE_DEPOT", "memory://cli-env")
    assert depot_of(argparse.Namespace(depot=None), fake).spec == "memory://cli-env"
    assert isinstance(depot_of(argparse.Namespace(depot="memory://cli-flag"), fake), MemoryDepot)
    monkeypatch.delenv("EMFORGE_DEPOT")
    assert _main("init", "--root", fake, "--depot", "memory://cli-init", "--profile", "fake_f1") == 0
    assert open_depot("memory://cli-init").exists(paths.strategies_yaml("fake_f1"))
    assert paths.registry_py(fake).exists() and paths.user_strategies_dir(fake).is_dir(), "本機程式碼仍在 root"


def test_run_with_memory_depot_forces_in_process(fake, capsys):
    """M12d：`memory://` 子行程看不到 → run 自動改 in-process 並印一行；磁碟上零狀態。"""
    from emforge.depot import open_depot
    assert _main("init", "--root", fake, "--depot", "memory://cli-run", "--profile", "fake_f1") == 0
    assert _main("run", "--root", fake, "--depot", "memory://cli-run", "--profile", "fake_f1", "--once") == 0
    assert "in-process" in capsys.readouterr().out
    assert open_depot("memory://cli-run").get_json(paths.status_json("fake_f1"))["tick"] == 1
    assert not (fake / paths.status_json("fake_f1")).exists()
    assert _main("status", "--root", fake, "--depot", "memory://cli-run", "--profile", "fake_f1") == 0
    assert '"tick": 1' in capsys.readouterr().out


def test_doctor_prints_selfcheck_and_clock_skew(fake, capsys, monkeypatch):
    """M12d：doctor 印 `depot.selfcheck()`；時鐘偏移是阻擋條件（租約全靠伺服器側 modified_at vs 本機 now）。"""
    from emforge.depot import file as fdep
    monkeypatch.setattr(doctor, "ansysedt_running", lambda: False)
    assert _main("doctor", "--root", fake) == 0
    assert "depot" in capsys.readouterr().out
    monkeypatch.setattr(fdep, "_probe_mtime", lambda path: time.time() + 3600)
    assert _main("doctor", "--root", fake) == 4
    assert "時鐘偏移" in capsys.readouterr().out


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
    fs.atomic_write_json(fake / paths.control_json("fake_f1"), {"resume_strategies": ["blind"], "by": "ricky"})
    rt.acquire_lock()
    rt.tick()
    rt.release_lock()
    assert rt.strategy_state("blind")["paused"] is False
    assert isinstance(rt, Runtime)


# ── M14：儀器層 CLI ──────────────────────────────────────────────────────────
def test_fleet_device_state_describe_and_estop_cli(fake, capsys, monkeypatch):
    from emforge.device import estop, states
    from emforge.device.instrument import Instrument
    monkeypatch.setattr(doctor, "ansysedt_running", lambda: False)
    assert _main("fleet", "--root", fake) == 0 and "無儀器" in capsys.readouterr().out
    inst = Instrument(fake, "216", depot=fake, work_root=fake / "work", sleep=lambda s: None, worker_ver="emforge=test",
                      sim_factory=lambda wd, p: testing.FakeSimulator(workdir=str(wd), profile=p))
    inst.start()
    inst.bind(P, inst.work.make("s1"), store="s1")
    inst.open()
    inst.close()
    inst.stop()
    states.write_state(fake, states.DeviceState(tag="218", state="busy", store="s2", owner="queue:s2"))
    os.utime(fake / paths.device_state("218"), (time.time() - 3600, time.time() - 3600))
    assert _main("fleet", "--root", fake) == 0
    out = capsys.readouterr().out
    assert "216" in out and "idle" in out and "218" in out and "offline" in out and "s2" in out
    assert _main("fleet", "--root", fake, "--mcp-config") == 0
    assert "emforge-216" in capsys.readouterr().out
    assert _main("device-state", "216", "--root", fake) == 0 and '"state": "idle"' in capsys.readouterr().out
    assert _main("device-state", "999", "--root", fake) == 1
    assert _main("device-describe", "216", "--root", fake) == 0
    out = capsys.readouterr().out
    assert "fake_f1" in out and "device_simulate" in out
    assert _main("device-describe", "999", "--root", fake) == 1
    # e-stop：engage 單機／全機／本機；clear 要 --confirm（唯一解除路徑＝CLI）
    assert _main("device-estop", "engage", "--root", fake, "--tag", "216", "--by", "ricky", "--reason", "冒煙") == 0
    assert estop.engaged(fake, fake, "216")["scope"] == "device" and estop.engaged(fake, fake, "218") is None
    assert _main("device-estop", "clear", "--root", fake, "--tag", "216") == 2, "沒 --confirm 拒"
    assert estop.engaged(fake, fake, "216") is not None
    assert _main("device-estop", "clear", "--root", fake, "--tag", "216", "--confirm") == 0
    assert estop.engaged(fake, fake, "216") is None
    assert _main("device-estop", "engage", "--root", fake, "--by", "ricky", "--reason", "全停") == 0
    assert estop.engaged(fake, fake, "218")["scope"] == "fleet"
    assert _main("fleet", "--root", fake) == 0 and "ESTOP" in capsys.readouterr().out
    assert _main("device-estop", "clear", "--root", fake, "--confirm") == 0 and estop.engaged(fake, fake, "218") is None
    assert _main("device-estop", "engage", "--root", fake, "--local", "--by", "ricky", "--reason", "磁碟") == 0
    assert paths.estop_local(fake).exists() and estop.engaged(fake, fake, "216")["scope"] == "local"
    assert _main("device-estop", "clear", "--root", fake, "--local", "--confirm") == 0 and not paths.estop_local(fake).exists()
    assert _main("device-estop", "engage", "--root", fake, "--by", "ricky") == 1, "engage 要 --reason"


# ── M15：MCP ────────────────────────────────────────────────────────────────
def test_fleet_mcp_config_has_url_and_bearer_header_placeholder(fake, capsys):
    import json
    from emforge.device import states
    states.write_state(fake, states.DeviceState(tag="216", url="http://10.0.0.216:8765/mcp"))
    states.write_state(fake, states.DeviceState(tag="218"))
    assert _main("fleet", "--root", fake, "--mcp-config") == 0
    cfg = json.loads(capsys.readouterr().out)["mcpServers"]
    assert cfg["emforge-216"] == {"type": "http", "url": "http://10.0.0.216:8765/mcp",
                                  "headers": {"Authorization": "Bearer ${EMFORGE_DEVICE_TOKEN}"}}
    assert cfg["emforge-218"]["url"] == "" and "headers" in cfg["emforge-218"]


def test_worker_serve_starts_mcp_thread_sharing_the_loop_instrument(fake, monkeypatch, capsys):
    from emforge.cli import loops
    seen = {}

    def fake_serve_in_thread(inst, **kw):
        seen.update(kw, tag=inst.tag, state=inst.state.state)
        return None, "http://127.0.0.1:8765/mcp"

    monkeypatch.setattr(loops.mcp_server, "serve_in_thread", fake_serve_in_thread)
    make_batch(fake, "s1")
    queue.Queue(fake).add(make_job("s1"))
    assert _main("worker", "--root", fake, "--machine-tag", "216", "--once", "--work-root", fake / "work",
                 "--serve", "--host", "127.0.0.1", "--port", "8765") == 0
    assert seen == {"host": "127.0.0.1", "port": 8765, "secret": None, "tag": "216", "state": "idle"}
    assert queue.Queue(fake).state("s1") == "done", "MCP 與 worker 迴圈共用同一台儀器，批照跑"
    ev = [e["event"] for e in fs.read_jsonl(fake / paths.device_log("216"))]
    assert ev[0] == "device_start" and ev[-1] == "device_stop"
    assert "mcp" in capsys.readouterr().out.lower()


def test_device_serve_cli_serves_instrument_without_picking_queue(fake, monkeypatch):
    from emforge.cli import device as dev
    seen = {}
    monkeypatch.setattr(dev.mcp_server, "serve", lambda inst, **kw: seen.update(kw, tag=inst.tag))
    monkeypatch.setenv("EMFORGE_DEVICE_TOKEN", "s3cret")
    make_batch(fake, "s1")
    queue.Queue(fake).add(make_job("s1"))
    assert _main("device-serve", "216", "--root", fake, "--host", "0.0.0.0", "--port", "9000", "--work-root", fake / "work") == 0
    assert seen == {"host": "0.0.0.0", "port": 9000, "secret": "s3cret", "tag": "216"}
    assert queue.Queue(fake).state("s1") == "queued", "device-serve 不撿佇列"
    ev = [e["event"] for e in fs.read_jsonl(fake / paths.device_log("216"))]
    assert ev[0] == "device_start" and ev[-1] == "device_stop"
    monkeypatch.delenv("EMFORGE_DEVICE_TOKEN")
    assert _main("device-serve", "216", "--root", fake, "--host", "0.0.0.0", "--work-root", fake / "work") == 1, "非 loopback 無 token 拒起"


# ── M16：正式機準備 ──────────────────────────────────────────────────────────
def test_device_simulate_cli_calls_tool_with_bearer_and_prints_token_hint(fake, monkeypatch, capsys):
    from emforge.cli import device as dev
    seen = {}

    def fake_call(url, tool, args, *, token=None, timeout_s=None):
        seen.update(url=url, tool=tool, args=args, token=token)
        return {"needs_confirm": True, "token": "abcd1234", "record_id": "0" * 16, "preview": {"profile": args["profile"]}}

    monkeypatch.setattr(dev.mcp_client, "call_device", fake_call)
    monkeypatch.setenv("EMFORGE_DEVICE_TOKEN", "s3cret")
    bits = "01" * 32
    assert _main("device-simulate", "--url", "http://10.0.0.216:8765/mcp", "--profile", "fake_f1", "--bits", bits, "--by", "ricky") == 0
    out = capsys.readouterr().out
    assert "abcd1234" in out and "--confirm" in out
    assert seen == {"url": "http://10.0.0.216:8765/mcp", "tool": "device_simulate", "token": "s3cret",
                    "args": {"profile": "fake_f1", "bits": bits, "by": "ricky"}}
    f = fake / "bits.txt"
    f.write_text(bits[:32] + "\n" + bits[32:] + "\n", encoding="utf-8")
    assert _main("device-simulate", "--url", "http://10.0.0.216:8765/mcp", "--profile", "fake_f1", "--bits", f"@{f}",
                 "--confirm", "abcd1234") == 0
    assert seen["args"]["confirm"] == "abcd1234" and seen["args"]["bits"].replace("\n", "") == bits
    monkeypatch.setattr(dev.mcp_client, "call_device", lambda *a, **k: (_ for _ in ()).throw(dev.mcp_client.DeviceCallFailed("device_busy: x")))
    assert _main("device-simulate", "--url", "http://10.0.0.216:8765/mcp", "--profile", "fake_f1", "--bits", bits) == 1
    assert "device_busy" in capsys.readouterr().err


def test_init_writes_limits_template_and_describe_shows_limits_source(root, capsys, monkeypatch):
    import json
    from emforge.device.instrument import Instrument
    monkeypatch.setattr(doctor, "ansysedt_running", lambda: False)
    assert _main("init", "--root", root, "--profile", "fake_f1") == 0
    lim = paths.limits_json(root)
    assert lim.exists() and json.loads(lim.read_text(encoding="utf-8"))["max_sample_s"] > 0
    lim.write_text(json.dumps({"max_sample_s": 1234}), encoding="utf-8")
    assert _main("init", "--root", root) == 0 and json.loads(lim.read_text(encoding="utf-8")) == {"max_sample_s": 1234}, "不覆寫"
    testing.make_fake_root(root)
    inst = Instrument(root, "216", depot=root, work_root=root / "work", sleep=lambda s: None, worker_ver="emforge=test",
                      sim_factory=lambda wd, p: testing.FakeSimulator(workdir=str(wd), profile=p))
    inst.start()
    inst.bind(P, inst.work.make("s1"), store="s1")
    inst.open()
    inst.close()
    inst.stop()
    capsys.readouterr()
    assert _main("device-describe", "216", "--root", root) == 0
    out = capsys.readouterr().out
    assert "1234" in out and "limits.json" in out


# ── 檢查 #13／#14（2026-09-07）：MCP 起不來要看得見；EMFORGE_MCP_PORT 壞值不炸別的命令 ──────
def test_worker_serve_reports_mcp_bind_failure_and_exits_1(fake, monkeypatch, capsys):
    from emforge.cli import loops

    def dead(inst, **kw):
        raise RuntimeError("MCP 埠 127.0.0.1:8765 綁不上（被佔？）")

    monkeypatch.setattr(loops.mcp_server, "serve_in_thread", dead)
    make_batch(fake, "s1")
    queue.Queue(fake).add(make_job("s1"))
    assert _main("worker", "--root", fake, "--machine-tag", "216", "--once", "--work-root", fake / "work",
                 "--serve", "--host", "127.0.0.1", "--port", "8765") == 1
    assert "綁不上" in capsys.readouterr().err
    assert queue.Queue(fake).state("s1") == "queued", "MCP 起不來就不跑 worker（處置與 check_bind 拒起對稱）"
    st = fs.read_json(fake / paths.device_state("216"))
    assert st["url"] is None
    ev = [e["event"] for e in fs.read_jsonl(fake / paths.device_log("216"))]
    assert ev[-1] == "device_stop" and "device_serve" not in ev


def test_device_serve_reports_bind_failure_and_exits_1(fake, monkeypatch, capsys):
    from emforge.cli import device as dev

    def dead(inst, **kw):
        raise RuntimeError("MCP 埠 127.0.0.1:9000 綁不上（被佔？）")

    monkeypatch.setattr(dev.mcp_server, "serve", dead)
    assert _main("device-serve", "216", "--root", fake, "--host", "127.0.0.1", "--port", "9000", "--work-root", fake / "work") == 1
    assert "綁不上" in capsys.readouterr().err
    ev = [e["event"] for e in fs.read_jsonl(fake / paths.device_log("216"))]
    assert ev[-1] == "device_stop"


def test_mcp_port_env_bad_value_only_bites_serve_commands_via_argparse(fake, monkeypatch, capsys):
    """以前 add_serve_flags 在建 parser 時就 int(環境變數)：EMFORGE_MCP_PORT="" 讓 version／--help／doctor 全吐 traceback
    （start_worker.cmd 誤報成 doctor 阻擋）。現在交給 argparse：只有用到 --port 的子命令才轉型，錯就 exit 2＋人話。"""
    monkeypatch.setenv("EMFORGE_MCP_PORT", "")
    assert _main("version") == 0
    monkeypatch.setenv("EMFORGE_MCP_PORT", "abc")
    assert _main("version") == 0
    with pytest.raises(SystemExit) as ei:
        _main("worker", "--root", fake, "--serve")
    assert ei.value.code == 2 and "EMFORGE_MCP_PORT" in capsys.readouterr().err
    monkeypatch.setenv("EMFORGE_MCP_PORT", "9123")
    from emforge.cli import build_parser
    assert build_parser().parse_args(["worker", "--root", str(fake)]).port == 9123


def test_worker_warns_when_machine_tag_comes_from_ip_probe(fake, monkeypatch, capsys):
    """檢查 #20：沒 --machine-tag 也沒 EMFORGE_MACHINE → 照舊用 IP 末段，但 stderr 要警告（VPN／DHCP 換 IP 會換身分）。"""
    monkeypatch.delenv("EMFORGE_MACHINE", raising=False)
    monkeypatch.setattr(cli.loops, "local_tag", lambda: "77")
    assert _main("worker", "--root", fake, "--once", "--work-root", fake / "work") == 0
    e = capsys.readouterr().err
    assert "EMFORGE_MACHINE" in e and "77" in e
    assert _main("worker", "--root", fake, "--once", "--work-root", fake / "work", "--machine-tag", "216") == 0
    assert "EMFORGE_MACHINE" not in capsys.readouterr().err


def test_device_estop_tag_and_local_are_mutually_exclusive_and_clear_nothing_exits_1(fake, capsys):
    """檢查 #21：--tag 與 --local 同給以前靜默忽略 --tag、印「已按下（本機）」卻沒對 216 做事；clear 沒東西可清以前回 0。"""
    with pytest.raises(SystemExit) as ei:
        _main("device-estop", "engage", "--root", fake, "--tag", "216", "--local", "--by", "r", "--reason", "x")
    assert ei.value.code == 2
    assert _main("device-estop", "clear", "--root", fake, "--tag", "216", "--confirm") == 1
    assert "本來就沒有" in capsys.readouterr().err
