# -*- coding: utf-8 -*-
"""tests/device/test_states.py — `emforge/device/states.py`：狀態字典欄位唯一真相、寫失敗不炸、機隊讀取推導 offline。"""
import dataclasses

from emforge import paths
from emforge.depot import MemoryDepot
from emforge.device import states

FIELDS = {"tag", "at", "state", "owner", "store", "profile", "profile_hash", "current_id", "sample_started_at", "n_done",
          "n_error", "last_result_at", "last_error", "worker_ver", "pid", "url", "free_gb", "ansysedt_running", "estop",
          "limits", "started_at"}


def test_device_state_fields_are_the_declared_set_and_roundtrip():
    assert {f.name for f in dataclasses.fields(states.DeviceState)} == FIELDS
    st = states.DeviceState(tag="216", state="ready", profile="fake_f1", n_done=3)
    d = st.to_dict()
    assert set(d) == FIELDS and d["state"] == "ready" and states.DeviceState.from_dict(d) == st
    assert states.DeviceState.from_dict({**d, "unknown_future_field": 1}).tag == "216", "未知鍵忽略（滾動升級）"
    assert "ready" in states.STATES and states.STATES[0] == "idle"


def test_write_state_swallows_depot_failure_and_logs(monkeypatch):
    d = MemoryDepot()
    st = states.DeviceState(tag="216")
    assert states.write_state(d, st) is True and d.get_json(paths.device_state("216"))["tag"] == "216"
    monkeypatch.setattr(d, "put_json", lambda key, obj: (_ for _ in ()).throw(OSError("NAS 斷線")))
    logged = []
    assert states.write_state(d, st, log=lambda event, **f: logged.append((event, f))) is False
    assert logged and logged[0][0] == "device_fault" and "NAS" in logged[0][1]["error"]
    assert states.write_state(d, st, log=lambda event, **f: (_ for _ in ()).throw(OSError("log 也斷"))) is False, "log 也失敗仍不炸"


def test_read_state_and_read_fleet_derive_offline_from_modified_at():
    d = MemoryDepot()
    assert states.read_state(d, "216") is None and states.read_fleet(d) == []
    states.write_state(d, states.DeviceState(tag="216", state="busy", store="s1"))
    states.write_state(d, states.DeviceState(tag="218", state="idle"))
    d.set_modified_at(paths.device_state("218"), d.now() - 3600)
    fleet = states.read_fleet(d, offline_s=90)
    assert [f["tag"] for f in fleet] == ["216", "218"]
    assert fleet[0]["offline"] is False and fleet[0]["age_s"] < 5 and fleet[0]["store"] == "s1"
    assert fleet[1]["offline"] is True and fleet[1]["age_s"] >= 3600
    assert states.read_state(d, "216").state == "busy"
