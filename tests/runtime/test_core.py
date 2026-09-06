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
    other = rt_root / paths.strategies_yaml("other_p")
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
    lk = rt_root / paths.runtime_lock("fake_f1")
    old = time.time() - 24 * 3600
    os.utime(lk, (old, old))
    r2 = make_rt(rt_root)
    r2.acquire_lock()                        # 心跳死了一天 → 可以破
    assert lk.exists() and fs.read_claim(lk)["pid"] == os.getpid()
    r2.release_lock()


def test_run_once_executes_single_tick_writes_status_and_events(rt):
    rc = rt.run(once=True)
    assert rc == 0
    st = fs.read_json(rt.root / paths.status_json("fake_f1"))
    assert st["profile"] == "fake_f1" and st["tick"] == 1 and st["profile_hash"] == rt.profile.profile_hash
    assert set(st["strategies"]) == {"top_k_flip", "blind"} and "inflight" in st and "db" in st
    ev = [e["event"] for e in fs.read_jsonl(rt.root / paths.events_jsonl("fake_f1"))]
    assert ev[0] == "runtime_start" and "batch_dispatched" in ev and ev[-1] == "runtime_stop"
    assert not (rt.root / paths.runtime_lock("fake_f1")).exists(), "結束釋放鎖"


def test_status_inflight_entries_carry_queue_state(rt):
    """review-2 配套：status.json 的 inflight 條目要能看出佇列狀態（queued／claimed／fail…），人才知道要不要 abandon。"""
    rt.acquire_lock()
    rt.tick()
    rt.release_lock()
    st = fs.read_json(rt.root / paths.status_json("fake_f1"))
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
    assert fs.read_json(rt_root / paths.state_json("fake_f1"))["tick"] == 1
    assert (rt_root / paths.inflight_file("fake_f1", "fake_f1-blind-t00001")).exists()
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
    lk = rt_root / paths.runtime_lock("fake_f1")
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
    fs.touch(rt.root / paths.runtime_stop("fake_f1"))
    rc = rt.run(once=False)
    assert rc == 0
    ev = [e["event"] for e in fs.read_jsonl(rt.root / paths.events_jsonl("fake_f1"))]
    assert ev[-1] == "runtime_stop" and "batch_dispatched" not in ev, "STOP 在 tick 之前檢查"


def test_state_persists_across_restart(rt_root, rt):
    rt.run(once=True)
    assert rt.state["tick"] == 1
    rt2 = make_rt(rt_root)
    rt2.run(once=True)
    assert rt2.state["tick"] == 2 and fs.read_json(rt_root / paths.state_json("fake_f1"))["tick"] == 2


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
    ev = [e for e in fs.read_jsonl(rt_root / paths.events_jsonl("fake_f1")) if e["event"] == "config_invalid"]
    assert ev and "batchh" in ev[0]["error"]


def test_yaml_missing_at_start_raises(root):
    from emforge import testing
    testing.make_fake_root(root)
    with pytest.raises(strategy.ConfigError):
        make_rt(root)


# ── M12d：Depot 化 ───────────────────────────────────────────────────────────
def test_heartbeat_after_lock_broken_does_not_recreate_lock(rt_root, rt):
    """M12d：鎖被別人破掉（或人清掉）後，心跳不能重建一個空鎖擋人——`Depot.touch` 缺即 no-op、不建檔。"""
    rt.acquire_lock()
    key = paths.runtime_lock("fake_f1")
    assert rt.depot.delete(key)
    rt.heartbeat()
    assert not rt.depot.exists(key), "心跳沒有重建鎖"
    r2 = make_rt(rt_root)
    r2.acquire_lock()                                     # 第二個實例拿得到
    rt.release_lock()                                     # 第一個實例收工：只刪自己的（owner 不符）→ 不動 r2 的
    assert rt.depot.owner(key)["pid"] == os.getpid() and rt.depot.exists(key), "r2 的鎖完好"
    r2.release_lock()
    assert not rt.depot.exists(key)


def test_inflight_skips_doc_deleted_between_list_and_get(rt, monkeypatch):
    """M12d：列舉可最終一致——列到了但 get 回 None（剛被 collect／abandon 拿掉）直接跳過，不塞 None 進去炸下游。"""
    rt.acquire_lock()
    rt.tick()
    rt.release_lock()
    keys = rt.depot.list(paths.inflight_dir("fake_f1"))
    assert keys
    real = rt.depot.get_json
    monkeypatch.setattr(rt.depot, "get_json", lambda key: None if key == keys[0] else real(key))
    assert len(rt.inflight()) == len(keys) - 1 and all(i is not None for i in rt.inflight())


def test_yaml_reload_triggers_on_content_change_without_mtime(rt_root, rt):
    """M12d：重載比內容 sha1，不看 mtime（S3 沒有可信 mtime；copy 工具也常保留舊 mtime）。
    內容變、mtime 不動 → 重讀；mtime 動、內容不動 → 不重讀（不發第二次 config_reloaded）。"""
    rt.acquire_lock()
    key = paths.strategies_yaml("fake_f1")
    m0 = rt.depot.modified_at(key)
    rt.depot.put_bytes(key, b"profile: fake_f1\nstrategies:\n  - {name: blind, prio: 9, batch: 2}\n")
    rt.depot.set_modified_at(key, m0)
    rt.reload_config()
    assert [s.name for s in rt.config.strategies] == ["blind"]
    rt.depot.set_modified_at(key, m0 + 100)
    rt.reload_config()
    ev = [e["event"] for e in rt.depot.read_log(paths.events_jsonl("fake_f1"))]
    assert ev.count("config_reloaded") == 1


def test_runtime_on_memory_depot_keeps_disk_free_of_state(root):
    """M12d：`Runtime(root, p, depot=MemoryDepot)`——root 只剩本機程式碼（registry.py／策略 workdir），狀態全在 depot。"""
    from emforge import testing
    from emforge.depot import MemoryDepot
    from tests.runtime.conftest import YAML
    depot = MemoryDepot()
    testing.make_fake_root(root, depot=depot)
    depot.put_bytes(paths.strategies_yaml("fake_f1"), YAML.encode("utf-8"))
    r = core.Runtime(root, "fake_f1", depot=depot, sleep=lambda s: None, propose_fn=strategy.propose_in_process)
    assert r.run(once=True) == 0
    assert depot.get_json(paths.status_json("fake_f1"))["tick"] == 1 and depot.list(paths.queue_dir())
    assert not (root / "db").exists() and not (root / "queue").exists() and not (root / "batches").exists()
    assert not (root / paths.status_json("fake_f1")).exists()


def test_quiet_fleet_waits(rt_root, rt):
    rt.acquire_lock()
    rt.tick()                                   # blind 派了一批
    inflight = list((rt_root / paths.inflight_dir("fake_f1")).glob("*.json"))
    assert inflight
    rt.config.runtime.quiet_s = 1
    old = time.time() - 3600
    for f in inflight:
        os.utime(f, (old, old))
    n_before = len(rt.queue.list())
    rt.tick()
    ev = [e["event"] for e in fs.read_jsonl(rt_root / paths.events_jsonl("fake_f1"))]
    assert "fleet_quiet" in ev and len(rt.queue.list()) == n_before, "機隊靜默 → 只等、不排程"


# ── M13：鎖丟了就停 ─────────────────────────────────────────────────────────
def test_runtime_stops_after_lock_lost(rt_root):
    """M13（收 §12-16）：鎖被破／被清後，下一圈發 lock_lost 並停（回 3），不再 tick。"""
    rt = make_rt(rt_root)
    key = paths.runtime_lock("fake_f1")
    rt._sleep = lambda s: rt.depot.delete(key)          # tick 之間有人清掉鎖
    rc = rt.run(once=False)
    assert rc == 3 and rt.lock_lost()
    log = rt.depot.read_log(paths.events_jsonl("fake_f1"))
    ev = [e["event"] for e in log]
    assert ev.count("batch_dispatched") >= 1 and ev[-2:] == ["lock_lost", "runtime_stop"]
    assert log[-1]["reason"] == "lock_lost" and rt.state["tick"] == 1, "只跑了一個 tick"


def test_heartbeat_detects_foreign_owner_as_lost_and_does_not_touch_it(rt_root, rt):
    rt.acquire_lock()
    key = paths.runtime_lock("fake_f1")
    rt.depot.delete(key)
    r2 = make_rt(rt_root)
    r2.acquire_lock()
    rt.depot.set_modified_at(key, 1_000_000)
    assert rt.heartbeat() is False and rt.lock_lost()
    assert rt.depot.modified_at(key) == 1_000_000, "別人的鎖不 touch"
    r2.release_lock()


# ── M14：機隊字典進 runtime ─────────────────────────────────────────────────
def test_fleet_quiet_counts_busy_instrument_heartbeat(rt_root, rt):
    """排隊≠停滯：inflight 檔與結果都老，但有儀器正拿著這批在跑（狀態字典新鮮）→ 不算靜默。"""
    from emforge.device import states
    rt.acquire_lock()
    rt.tick()
    rt.config.runtime.quiet_s = 1
    old = time.time() - 3600
    for inf in rt.inflight():
        rt.depot.set_modified_at(paths.inflight_file("fake_f1", inf["store"]), old)
    assert rt.fleet_quiet() is True
    store = rt.inflight()[0]["store"]
    states.write_state(rt.depot, states.DeviceState(tag="216", state="busy", owner=f"queue:{store}", store=store))
    assert rt.fleet_quiet() is False, "忙碌儀器的心跳算進度"
    rt.depot.set_modified_at(paths.device_state("216"), old)
    assert rt.fleet_quiet() is True, "儀器心跳也老了（離線）→ 靜默"
    rt.release_lock()


def test_status_carries_fleet_summary(rt):
    from emforge.device import states
    states.write_state(rt.depot, states.DeviceState(tag="218", state="idle", n_done=5))
    rt.acquire_lock()
    rt.tick()
    rt.release_lock()
    fleet = rt.depot.get_json(paths.status_json("fake_f1"))["fleet"]
    assert fleet[0]["tag"] == "218" and fleet[0]["state"] == "idle" and fleet[0]["offline"] is False and fleet[0]["n_done"] == 5
