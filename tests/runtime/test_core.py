# -*- coding: utf-8 -*-
"""tests/runtime/test_core.py — `emforge/runtime/core.py`：單例鎖、設定重載、tick 骨架、state／status、STOP、機隊靜默。"""
import os
import time

import pytest

from emforge import fs, paths, strategy
from emforge.runtime import core
from tests.runtime.conftest import make_rt, write_yaml


def test_lock_refuses_second_instance_same_profile_allows_other(rt_root, rt):
    rt.acquire_lock()
    with pytest.raises(core.RuntimeLocked):
        make_rt(rt_root).acquire_lock()
    other = paths.strategies_yaml(rt_root, "other_p")
    other.parent.mkdir(parents=True)
    other.write_text("profile: other_p\nstrategies: []\n", encoding="utf-8")
    from emforge import model, profiles, testing
    import dataclasses
    profiles.register_profile(dataclasses.replace(testing.FAKE_PROFILE, name="other_p"))
    r2 = core.Runtime(rt_root, "other_p", sleep=lambda s: None, propose_fn=strategy.propose_in_process)
    r2.acquire_lock()
    r2.release_lock()
    assert model  # 用到 import 避免 lint


def test_lock_with_stale_heartbeat_broken(rt_root, rt):
    rt.acquire_lock()
    lk = paths.runtime_lock(rt_root, "fake_f1")
    old = time.time() - 24 * 3600
    os.utime(lk, (old, old))
    r2 = make_rt(rt_root)
    r2.acquire_lock()                        # 心跳死了一天 → 可以破
    assert lk.exists() and fs.read_claim(lk)["pid"] == os.getpid()
    r2.release_lock()


def test_run_once_executes_single_tick_writes_status_and_events(rt):
    rc = rt.run(once=True)
    assert rc == 0
    st = fs.read_json(paths.status_json(rt.root, "fake_f1"))
    assert st["profile"] == "fake_f1" and st["tick"] == 1 and st["profile_hash"] == rt.profile.profile_hash
    assert set(st["strategies"]) == {"top_k_flip", "blind"} and "inflight" in st and "db" in st
    ev = [e["event"] for e in fs.read_jsonl(paths.events_jsonl(rt.root, "fake_f1"))]
    assert ev[0] == "runtime_start" and "batch_dispatched" in ev and ev[-1] == "runtime_stop"
    assert not paths.runtime_lock(rt.root, "fake_f1").exists(), "結束釋放鎖"


def test_status_inflight_entries_carry_queue_state(rt):
    """review-2 配套：status.json 的 inflight 條目要能看出佇列狀態（queued／claimed／fail…），人才知道要不要 abandon。"""
    rt.acquire_lock()
    rt.tick()
    rt.release_lock()
    st = fs.read_json(paths.status_json(rt.root, "fake_f1"))
    assert st["inflight"] and st["inflight"][0]["queue_state"] == "queued"


def test_tick_number_is_saved_before_dispatch_so_crash_replay_uses_next_tick(rt_root, rt, monkeypatch):
    """回歸 review-3：tick 號若在 tick 結束才存，中途死掉重啟會重播同一 tick → store 名撞既有批。
    現在 tick 號一加就落地；重啟從下一號開始。"""
    rt.acquire_lock()
    real_schedule = core._schedule.schedule
    died = []

    def dispatch_then_die(r):
        real_schedule(r)
        if not died:
            died.append(1)
            raise RuntimeError("死在派工之後、tick 尾的 save_state 之前")

    monkeypatch.setattr(core._schedule, "schedule", dispatch_then_die)
    with pytest.raises(RuntimeError):
        rt.tick()                                         # blind 已派 t00001、然後死
    rt.release_lock()
    assert fs.read_json(paths.state_json(rt_root, "fake_f1"))["tick"] == 1
    assert paths.inflight_file(rt_root, "fake_f1", "fake_f1-blind-t00001").exists()
    rt2 = make_rt(rt_root)
    assert rt2.run(once=True) == 0, "重啟不會撞 BatchExists"
    assert rt2.state["tick"] == 2


def test_readonly_runtime_refuses_save_and_lock(rt_root):
    """回歸 review-4：CLI（smoke／abandon）建的 Runtime 是唯讀的——不准寫 state.json、不准拿鎖。"""
    ro = make_rt(rt_root, readonly=True)
    with pytest.raises(RuntimeError, match="readonly"):
        ro.save_state()
    with pytest.raises(RuntimeError, match="readonly"):
        ro.acquire_lock()


def test_heartbeat_thread_keeps_lock_fresh_during_long_tick(rt_root):
    """回歸 review-7：一個 tick 可能超過鎖的 stale 門檻（策略子行程逐個逾時）；背景心跳讓第二個實例拿不到鎖。"""
    rt = make_rt(rt_root, heartbeat_s=0.05)
    rt.acquire_lock()
    lk = paths.runtime_lock(rt_root, "fake_f1")
    old = time.time() - 24 * 3600
    os.utime(lk, (old, old))
    rt.start_heartbeat()
    try:
        time.sleep(0.4)                                   # 模擬「很久的 tick」期間
        assert time.time() - fs.mtime(lk) < 5, "心跳有在 touch"
        with pytest.raises(core.RuntimeLocked):
            make_rt(rt_root).acquire_lock()
    finally:
        rt.stop_heartbeat()
        rt.release_lock()
    assert not lk.exists()


def test_stop_file_exits_after_tick(rt):
    fs.touch(paths.runtime_stop(rt.root, "fake_f1"))
    rc = rt.run(once=False)
    assert rc == 0
    ev = [e["event"] for e in fs.read_jsonl(paths.events_jsonl(rt.root, "fake_f1"))]
    assert ev[-1] == "runtime_stop" and "batch_dispatched" not in ev, "STOP 在 tick 之前檢查"


def test_state_persists_across_restart(rt_root, rt):
    rt.run(once=True)
    assert rt.state["tick"] == 1
    rt2 = make_rt(rt_root)
    rt2.run(once=True)
    assert rt2.state["tick"] == 2 and fs.read_json(paths.state_json(rt_root, "fake_f1"))["tick"] == 2


def test_yaml_reload_on_mtime_invalid_keeps_last_good(rt_root, rt):
    rt.acquire_lock()
    assert [s.name for s in rt.config.strategies] == ["top_k_flip", "blind"]
    y = write_yaml(rt_root, "profile: fake_f1\nstrategies:\n  - {name: blind, prio: 9, batch: 2}\n")
    os.utime(y, (time.time() + 5, time.time() + 5))
    rt.reload_config()
    assert [s.name for s in rt.config.strategies] == ["blind"]
    y = write_yaml(rt_root, "profile: fake_f1\nstrategies:\n  - {name: blind, prio: 9, batchh: 2}\n")
    os.utime(y, (time.time() + 10, time.time() + 10))
    rt.reload_config()
    assert [s.name for s in rt.config.strategies] == ["blind"] and rt.config.strategies[0].batch == 2, "沿用上次有效"
    ev = [e for e in fs.read_jsonl(paths.events_jsonl(rt_root, "fake_f1")) if e["event"] == "config_invalid"]
    assert ev and "batchh" in ev[0]["error"]


def test_yaml_missing_at_start_raises(root):
    from emforge import testing
    testing.make_fake_root(root)
    with pytest.raises(strategy.ConfigError):
        make_rt(root)


def test_quiet_fleet_waits(rt_root, rt):
    rt.acquire_lock()
    rt.tick()                                   # blind 派了一批
    inflight = list(paths.inflight_dir(rt_root, "fake_f1").glob("*.json"))
    assert inflight
    rt.config.runtime.quiet_s = 1
    old = time.time() - 3600
    for f in inflight:
        os.utime(f, (old, old))
    n_before = len(rt.queue.list())
    rt.tick()
    ev = [e["event"] for e in fs.read_jsonl(paths.events_jsonl(rt_root, "fake_f1"))]
    assert "fleet_quiet" in ev and len(rt.queue.list()) == n_before, "機隊靜默 → 只等、不排程"
