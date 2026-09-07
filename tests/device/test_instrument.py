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


def test_simulate_once_refusal_does_not_burn_token_and_estop_raises_before_lease(inst):
    """token 綁 10 分鐘窗、同窗內同值：被拒（busy／急停）就燒掉＝十分鐘內不能重試。拒絕在 consume 之前發生。"""
    inst.start()
    bits = some_bits(8)
    tok = inst.simulate_once(P.name, bits, by="ricky")["token"]
    inst.acquire("queue:s9")
    with pytest.raises(I.DeviceBusy):
        inst.simulate_once(P.name, bits, by="ricky", confirm=tok)
    inst.release("queue:s9")
    estop.engage(inst.depot, None, by="x", reason="r")
    with pytest.raises(estop.EstopEngaged):
        inst.simulate_once(P.name, bits, by="ricky", confirm=tok)
    assert inst.state.owner is None, "急停在搶租約之前就拒，不留租約"
    estop.clear(inst.depot, None)
    assert inst.simulate_once(P.name, bits, by="ricky", confirm=tok)["status"] == "done"
    with pytest.raises(I.ConfirmRejected):
        inst.simulate_once(P.name, bits, by="ricky", confirm=tok)


def test_simulate_once_precondition_failure_raises_not_error_result(root):
    inst = make_inst(root, limits=Limits(allowed_profiles=("other_p",)))
    inst.start()
    tok = inst.simulate_once(P.name, some_bits(9), by="ricky")["token"]
    with pytest.raises(I.PreconditionFailed, match="allowed"):
        inst.simulate_once(P.name, some_bits(9), by="ricky", confirm=tok)
    assert inst.state.owner is None and inst.state.state == "idle"
    inst.stop()


def test_instrument_reads_limits_from_root_file_when_not_given(root):
    paths.limits_json(root).parent.mkdir(parents=True, exist_ok=True)
    paths.limits_json(root).write_text('{"allowed_profiles": ["other_p"]}', encoding="utf-8")
    inst = make_inst(root)
    assert inst.limits.allowed_profiles == ("other_p",) and inst.limits_source.endswith("limits.json")
    inst.start()
    inst.bind(P, inst.work.make("s1"), store="s1")
    with pytest.raises(I.PreconditionFailed, match="allowed"):
        inst.open()
    inst.stop()
    assert make_inst(root, limits=Limits()).limits_source == "explicit"


def test_announce_url_lands_in_state_dict(inst):
    inst.start()
    inst.announce_url("http://127.0.0.1:8765/mcp")
    assert _state(inst)["url"] == "http://127.0.0.1:8765/mcp"


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


# ── 檢查 #1（2026-09-07）：MCP 租約不重入 ─────────────────────────────────────
def test_mcp_lease_never_reenters_only_queue_owner_may_resume(inst):
    assert inst.acquire("queue:s1") and inst.acquire("queue:s1"), "worker 同一批續跑可重入"
    assert inst.release("queue:s1")
    assert inst.acquire("mcp:ricky") is True and inst.acquire("mcp:ricky") is False, "MCP 同名不重入"
    assert inst.release("mcp:ricky")


def test_concurrent_simulate_once_with_default_by_runs_exactly_one(root):
    """repro t2_lease：兩個呼叫者都不帶 by（LLM 一輪並行兩個 tool call）→ 以前同 owner 重入、同機兩個模擬器、
    先跑完的把對方的關掉、租約放掉。現在：恰一筆跑、另一筆 DeviceBusy、只建一個模擬器、每個開過的都被 close。"""
    sims = []

    def factory(wd, p):
        s = testing.FakeSimulator(workdir=str(wd), profile=p, delay_s=0.4)
        sims.append(s)
        return s

    inst = make_inst(root, sim_factory=factory)
    inst.start()
    b1, b2 = some_bits(11), some_bits(12)
    t1 = inst.simulate_once(P.name, b1, by="mcp")["token"]
    t2 = inst.simulate_once(P.name, b2, by="mcp")["token"]
    out, go = {}, threading.Barrier(2)

    def run(name, b, tok):
        go.wait()
        try:
            out[name] = inst.simulate_once(P.name, b, by="mcp", confirm=tok)["status"]
        except I.DeviceBusy:
            out[name] = "busy"

    ts = [threading.Thread(target=run, args=("A", b1, t1)), threading.Thread(target=run, args=("B", b2, t2))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(out.values()) == ["busy", "done"], out
    assert len(sims) == 1 and sims[0].calls["open"] == 1 and sims[0].calls["close"] == 1, "只開一個、而且關了"
    assert inst.state.owner is None and inst.sim is None and inst._bound is None
    inst.stop()


# ── 檢查 #9（2026-09-07）：confirm token 每次不同、綁 op/key、會過期 ─────────────
def test_confirm_tokens_unique_per_issue_bound_to_op_key_and_expire(root):
    """以前 token＝sha1(secret|op|key|10 分鐘窗)：同窗同值、用過即燒 → 同一片十分鐘內量不了第二次，錯誤訊息還叫人「重拿」（拿回同一個）。
    現在每次發的 token 帶 nonce：各自有效、消費即失效、綁 op/key、10 分鐘到期。"""
    clock = [1_000.0]
    inst = make_inst(root, clock=lambda: clock[0])
    inst.start()
    bits = some_bits(13)
    t1 = inst.simulate_once(P.name, bits, by="ricky")["token"]
    t2 = inst.simulate_once(P.name, bits, by="ricky")["token"]
    assert t1 != t2 and len(t1) == len(t2) == 8, "同 op/key 每次發的 token 都不同"
    assert inst.simulate_once(P.name, bits, by="ricky", confirm=t1)["status"] == "done"
    assert inst.simulate_once(P.name, bits, by="ricky", confirm=t2)["status"] == "done", "同一片十分鐘內量第二次"
    with pytest.raises(I.ConfirmRejected):
        inst.simulate_once(P.name, bits, by="ricky", confirm=t2)                       # 重放
    stop_tok = inst.issue_confirm("stop_worker", "queue/STOP.216")
    assert inst.check_confirm("resume_worker", "queue/STOP.216", stop_tok) is False, "stop 的 token 不能拿去 resume"
    assert inst.check_confirm("stop_worker", "queue/STOP.216", stop_tok) is True
    clock[0] += inst.limits.confirm_window_s + 1
    assert inst.check_confirm("stop_worker", "queue/STOP.216", stop_tok) is False, "過期"
    inst.stop()
