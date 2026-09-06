# -*- coding: utf-8 -*-
"""tests/device/test_instrument.py — `emforge/device/instrument.py`：狀態機、租約、e-stop、前置檢查、兩段式 simulate_once。

MHS 對應：states＝DeviceState；procedures＝open/simulate/kill/close/abort/selfcheck/simulate_once；limits＝Limits；
safety＝allowed_profiles／Limits／preconditions／confirm／e-stop。M15 的 MCP tools 一對一貼在這些方法上。
"""
import threading
import time

import pytest

from emforge import paths, testing
from emforge.db import Database
from emforge.device import estop, instrument as I
from emforge.device.limits import Limits
from tests.device.conftest import P, fake_factory, make_inst, some_bits


def _log_events(inst):
    return [e["event"] for e in inst.depot.read_log(paths.device_log(inst.tag))]


def _state(inst):
    return inst.depot.get_json(paths.device_state(inst.tag))


def test_lifecycle_transitions_and_state_dict_written_each_step(inst):
    inst.start()
    assert _state(inst)["state"] == "idle" and _state(inst)["pid"] and _state(inst)["worker_ver"] == "emforge=test"
    inst.bind(P, inst.work.make("s1"), store="s1")
    inst.open()
    st = _state(inst)
    assert st["state"] == "ready" and st["profile"] == P.name and st["profile_hash"] == P.profile_hash and st["store"] == "s1"
    out = inst.simulate(some_bits())
    assert out.response.shape == (2, 17)
    st = _state(inst)
    assert st["state"] == "ready" and st["n_done"] == 1 and st["last_result_at"] and st["current_id"] is None
    inst.close()
    assert _state(inst)["state"] == "idle" and _state(inst)["profile"] is None
    inst.stop()
    assert _log_events(inst)[0] == "device_start" and _log_events(inst)[-1] == "device_stop"
    assert [s for s, _ in inst.history] == ["idle", "opening", "ready", "busy", "ready", "idle"]


def test_acquire_is_exclusive_refusal_logged_and_release_only_own(inst):
    inst.start()
    assert inst.acquire("queue:s1") is True and inst.acquire("queue:s1") is True, "同 owner 可重入"
    assert inst.acquire("mcp:ricky") is False
    ev = inst.depot.read_log(paths.device_log("216"))[-1]
    assert ev["event"] == "lease_refused" and ev["owner"] == "mcp:ricky" and ev["holder"] == "queue:s1"
    assert _state(inst)["owner"] == "queue:s1"
    assert inst.release("mcp:ricky") is False and inst.release("queue:s1") is True and _state(inst)["owner"] is None
    assert inst.acquire("mcp:ricky") is True


def test_open_refused_when_estop_engaged_and_state_follows(inst):
    inst.start()
    estop.engage(inst.depot, "216", by="ricky", reason="冒煙")
    inst.bind(P, inst.work.make("s1"), store="s1")
    with pytest.raises(estop.EstopEngaged):
        inst.open()
    assert _state(inst)["state"] == "estop" and _state(inst)["estop"]["scope"] == "device"
    assert "estop_engaged" in _log_events(inst)
    estop.clear(inst.depot, "216")
    assert inst.estop_engaged() is None and _state(inst)["state"] == "idle"
    assert _log_events(inst)[-1] == "estop_cleared"
    inst.open()
    assert _state(inst)["state"] == "ready"


def test_simulate_refused_when_estop_engaged_after_open(inst):
    inst.start()
    inst.bind(P, inst.work.make("s1"), store="s1")
    inst.open()
    estop.engage(inst.depot, None, by="ricky", reason="全停")
    with pytest.raises(estop.EstopEngaged) as ei:
        inst.simulate(some_bits())
    assert ei.value.scope == "fleet" and _state(inst)["state"] == "estop"


def test_open_failure_goes_fault_then_close_returns_idle(root):
    class _Bad(testing.FakeSimulator):
        def open(self):
            raise RuntimeError("COM 沒回應")

    inst = make_inst(root, sim_factory=lambda wd, p: _Bad(workdir=str(wd), profile=p))
    inst.start()
    inst.bind(P, inst.work.make("s1"), store="s1")
    with pytest.raises(RuntimeError):
        inst.open()
    st = _state(inst)
    assert st["state"] == "fault" and "COM" in st["last_error"] and "device_fault" in _log_events(inst)
    inst.close()
    assert _state(inst)["state"] == "idle"
    inst.stop()


def test_simulate_error_counts_n_error_and_reraises(root):
    rid_bits = some_bits(3)
    from emforge.model import record_id
    inst = make_inst(root, sim_factory=fake_factory(fail_ids={record_id(rid_bits, P.name)}))
    inst.start()
    inst.bind(P, inst.work.make("s1"), store="s1")
    inst.open()
    with pytest.raises(testing.FakeFailure):
        inst.simulate(rid_bits)
    st = _state(inst)
    assert st["state"] == "ready" and st["n_error"] == 1 and "fake failure" in st["last_error"] and st["n_done"] == 0
    inst.simulate(some_bits(4))
    assert _state(inst)["n_done"] == 1 and inst.median_time_s() == 0.0
    inst.stop()


def test_preconditions_block_open_and_keep_idle(root):
    inst = make_inst(root, limits=Limits(allowed_profiles=("other_p",)))
    inst.start()
    inst.bind(P, inst.work.make("s1"), store="s1")
    with pytest.raises(I.PreconditionFailed, match="allowed"):
        inst.open()
    assert _state(inst)["state"] == "idle" and _state(inst)["limits"]["allowed_profiles"] == ["other_p"]
    inst.stop()


def test_kill_and_abort_work_cross_thread_without_lease(inst):
    inst.start()
    inst.bind(P, inst.work.make("s1"), store="s1")
    inst.open()
    sim = inst.sim
    t = threading.Thread(target=lambda: inst.abort(by="ricky"))
    t.start()
    t.join()
    assert sim.calls["kill"] == 1 and _log_events(inst)[-1] == "device_abort"
    inst.kill()
    assert sim.calls["kill"] == 2


def test_simulate_once_two_step_confirm_writes_adhoc_not_db(inst):
    inst.start()
    bits = some_bits(5)
    r1 = inst.simulate_once(P.name, bits, by="ricky")
    assert r1["needs_confirm"] is True and len(r1["token"]) == 8 and r1["record_id"] and r1["preview"]["profile"] == P.name
    r2 = inst.simulate_once(P.name, bits, by="ricky", confirm=r1["token"])
    assert r2["status"] == "done" and r2["id"] == r1["record_id"] and r2["by"] == "ricky" and r2["adhoc"] is True
    keys = inst.depot.list(paths.adhoc_dir("216"))
    assert len(keys) == 1 and keys[0].endswith(f"-{r1['record_id']}.json")
    assert Database(inst.depot).metas(P.name) == [], "不入 db"
    assert _state(inst)["state"] == "idle" and _state(inst)["owner"] is None, "跑完關掉、租約放掉"
    assert _log_events(inst)[-1] == "device_simulate"
    with pytest.raises(I.ConfirmRejected):
        inst.simulate_once(P.name, bits, by="ricky", confirm=r1["token"])
    with pytest.raises(I.ConfirmRejected):
        inst.simulate_once(P.name, bits, by="ricky", confirm="00000000")


def test_simulate_once_refused_when_lease_held_and_rejects_bad_bits(inst):
    inst.start()
    bits = some_bits(6)
    tok = inst.simulate_once(P.name, bits, by="ricky")["token"]
    inst.acquire("queue:s9")
    with pytest.raises(I.DeviceBusy):
        inst.simulate_once(P.name, bits, by="ricky", confirm=tok)
    inst.release("queue:s9")
    with pytest.raises(ValueError, match="bad_bits"):
        inst.simulate_once(P.name, bits[:4], by="ricky")
    bad = some_bits(7)
    bad[P.fixed_on] = False
    with pytest.raises(ValueError, match="bad_bits"):
        inst.simulate_once(P.name, bad, by="ricky")


def test_heartbeat_refreshes_state_and_follows_estop(root):
    inst = make_inst(root, heartbeat_s=0.02)
    inst.start()
    estop.engage(inst.depot, None, by="x", reason="r")
    for _ in range(100):
        if _state(inst)["state"] == "estop":
            break
        time.sleep(0.02)
    assert _state(inst)["state"] == "estop"
    estop.clear(inst.depot, None)
    for _ in range(100):
        if _state(inst)["state"] == "idle":
            break
        time.sleep(0.02)
    assert _state(inst)["state"] == "idle"
    inst.stop()
    assert not inst._hb or not inst._hb.is_alive()


def test_selfcheck_reports_depot_health_and_state(inst, monkeypatch):
    from emforge import doctor
    monkeypatch.setattr(doctor, "ansysedt_running", lambda: False)
    inst.start()
    sc = inst.selfcheck()
    assert sc["depot"] == [] and sc["health"]["blocking"] == [] and sc["state"] == "idle" and sc["problems"] == []
