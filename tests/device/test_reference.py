# -*- coding: utf-8 -*-
"""tests/device/test_reference.py — `emforge/device/reference.py`：儀器說明檔（MHS 的自動產生自然語言說明）。

內容＝Profile 註冊表（限 allowed）＋Limits＋體檢＋近期 time_s 中位數＋程序清單（＝M15 的 MCP tools 名單）。
儀器 open 成功與每小時各刷一次；寫失敗不炸。
"""
from emforge import doctor, paths
from emforge.device import reference
from emforge.device.limits import Limits
from tests.device.conftest import P, make_inst, some_bits


def test_render_lists_profiles_limits_health_procedures(root, monkeypatch):
    monkeypatch.setattr(doctor, "ansysedt_running", lambda: False)
    inst = make_inst(root, limits=Limits(allowed_profiles=(P.name,)))
    md, d = reference.render(inst)
    assert d["tag"] == "216" and d["worker_ver"] == "emforge=test" and len(d["generated_at"]) == 19
    assert [p["name"] for p in d["profiles"]] == [P.name] and d["profiles"][0]["profile_hash"] == P.profile_hash
    assert d["limits"]["allowed_profiles"] == [P.name] and d["health"]["blocking"] == [] and d["median_time_s"] is None
    names = [p["name"] for p in d["procedures"]]
    assert {"device_state", "device_describe", "device_selfcheck", "device_log", "device_simulate", "device_abort",
            "device_estop", "device_stop_worker", "device_resume_worker"} <= set(names)
    assert all(isinstance(p["mutating"], bool) and p["description"] for p in d["procedures"])
    assert not any(p["name"] == "device_estop_clear" for p in d["procedures"]), "解除急停不在程序清單（CLI only）"
    for must in ("# 儀器 216", P.name, "device_simulate", "急停", "allowed_profiles"):
        assert must in md
    inst.stop()


def test_reference_written_on_open_and_hourly(root, monkeypatch):
    monkeypatch.setattr(doctor, "ansysedt_running", lambda: False)
    t = [1_000_000.0]
    inst = make_inst(root, clock=lambda: t[0])
    inst.start()
    assert not inst.depot.exists(paths.device_reference_md("216"))
    inst.bind(P, inst.work.make("s1"), store="s1")
    inst.open()
    assert inst.depot.exists(paths.device_reference_md("216")) and inst.depot.get_json(paths.device_reference_json("216"))["tag"] == "216"
    inst.simulate(some_bits())
    inst.depot.delete(paths.device_reference_md("216"))
    t[0] += 100
    inst._beat()
    assert not inst.depot.exists(paths.device_reference_md("216")), "一小時內不重寫"
    t[0] += 3600
    inst._beat()
    assert inst.depot.exists(paths.device_reference_md("216"))
    assert inst.depot.get_json(paths.device_reference_json("216"))["median_time_s"] == 0.0
    inst.stop()


def test_write_reference_swallows_depot_failure(root, monkeypatch):
    inst = make_inst(root)
    monkeypatch.setattr(inst.depot, "put_bytes", lambda k, b: (_ for _ in ()).throw(OSError("NAS")))
    assert reference.write_reference(inst) is False
    inst.stop()
