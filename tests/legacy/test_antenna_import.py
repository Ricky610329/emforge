# -*- coding: utf-8 -*-
"""tests/legacy/test_antenna_import.py — `emforge/legacy/antenna_import.py`：舊 NAS 資料 → db/<profile>/。

舊樹：`<store>_input/manifest.json + <id>.pt`（float32 25×25）、`<store>/results.json`（id → entry）、
`<store>/<sha>.pt`（SampleStore 的 (x, y) tuple，內容 hash 命名、無法從 id 反推）、`<store>/hfss_setup.json`、
`jobs_state/<store>.done`。join 靠 pattern bytes；量測**重算**不抄；`--verify` 證明新尺＝舊尺；只讀舊樹。
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pytest

from emforge import cli, model, paths, testing
from emforge.adapters.antenna import measure as am
from emforge.adapters.antenna import profiles as aprof
from emforge.db import Database
from emforge.legacy import antenna_import as li

torch = pytest.importorskip("torch")

DUAL, SINGLE = ("S11", "S21", "S22"), ("S11", "Gain")


def _pat(seed):
    p = np.random.default_rng(seed).random((25, 25)) > 0.5
    p[20:25, 10:15] = True
    return p


def _resp(seed, n_labels):
    return np.random.default_rng(1000 + seed).uniform(-30, 0, (n_labels, 17)).astype(np.float32)


def _wm_entry(y, labels):
    """舊 results.json 的 wm 欄：[每 label 主 margin…, worst]，2 位小數。"""
    if len(labels) == 3:
        wm, per = am.worst_margin_dual(y, labels, am.DUAL_R1_TARGETS)
        cols = [per["S11"], per["S21"], per["S22"]]
        extra = {k: round(per[k], 3) for k in ("m1", "m2", "m3", "m4", "m5", "m6")}
    else:
        wm, per = am.worst_margin(y, labels, am.SINGLE_BASE_TARGETS)
        cols = [per["S11"], per["Gain"]]
        extra = {}
    return {"wm": [round(c, 2) for c in cols] + [round(wm, 2)], "time_s": 100.0, **extra}


def write_store(old, store, items, *, port, setup=None, geom=None, dbw=None, machine="140.213.106.216", rad=False):
    """items: list of dict(seed, kind='orig', arm=None, parent=None, error=False, y_seed=None, wm_override=None)。"""
    labels = DUAL if port == "dual" else SINGLE
    inp, st = old / f"{store}_input", old / store
    inp.mkdir(parents=True, exist_ok=True)
    st.mkdir(parents=True, exist_ok=True)
    manifest, results, ids = [], {}, []
    for i, it in enumerate(items):
        lid = it.get("id", f"{store}_{i:03d}")
        ids.append(lid)
        p = _pat(it["seed"])
        torch.save(torch.tensor(p, dtype=torch.float32), inp / f"{lid}.pt")
        m = {"id": lid, "kind": it.get("kind", "orig")}
        if port == "dual":
            m["port"] = "dual"
        for k in ("arm", "parent", "parent_id", "source_id"):
            if k in it:
                m[k] = it[k]
        manifest.append(m)
        if it.get("error"):
            results[lid] = {"error": "COM 例外", "attempts": 3}
            continue
        y = _resp(it.get("y_seed", it["seed"]), len(labels))
        torch.save((torch.tensor(p, dtype=torch.float32), torch.tensor(y)), st / (hashlib.sha1(p.tobytes() + y.tobytes()).hexdigest()[:16] + ".pt"))
        entry = _wm_entry(y, labels)
        if geom:
            entry["geom"] = geom
        if dbw is not None:
            entry["dbw"] = dbw
        if "wm_override" in it:
            entry["wm"][0] = it["wm_override"]
        results[lid] = entry
        if rad:
            (st / "rad").mkdir(exist_ok=True)
            torch.save({"theta": torch.arange(3.0), "phi0": torch.ones(3), "phi90": torch.zeros(3)}, st / "rad" / f"{lid}.pt")
    (inp / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (st / "results.json").write_text(json.dumps(results), encoding="utf-8")
    if setup is not None:
        (st / "hfss_setup.json").write_text(json.dumps(setup), encoding="utf-8")
    js = old / "jobs_state"
    js.mkdir(exist_ok=True)
    (js / f"{store}.done").write_text(json.dumps({"machine": machine, "at": "x"}), encoding="utf-8")
    return ids


def write_harvest(old, store, n, port):
    st = old / store
    st.mkdir(parents=True, exist_ok=True)
    labels = DUAL if port == "dual" else SINGLE
    for i in range(n):
        p, y = _pat(500 + i), _resp(500 + i, len(labels))
        torch.save((torch.tensor(p, dtype=torch.float32), torch.tensor(y)), st / f"h{i:04d}.pt")


@pytest.fixture
def old(tmp_path):
    return tmp_path / "old_nas"


@pytest.fixture
def out(root):
    testing.make_fake_root(root)
    aprof.register_all()
    return root


# ── 映射 ────────────────────────────────────────────────────────────────────
def test_auto_mapping_table(old, out):
    write_store(old, "dedust_smp001a", [dict(seed=1)], port="dual", setup={"diag_bridge_w": 0.075}, geom="p01", dbw=0.075)
    write_store(old, "dedust_old", [dict(seed=2)], port="dual")
    write_store(old, "dedust_p01plain", [dict(seed=3)], port="dual", geom="p01")
    write_store(old, "dedust_s01", [dict(seed=4)], port="single")
    write_store(old, "handoff_ant_db100", [dict(seed=5)], port="single", setup={"diag_bridge_w": 0.1})
    write_store(old, "dedust_r60slot", [dict(seed=6)], port="dual", setup={"slot_spec": [[1, 2]], "diag_bridge_w": 0.075}, geom="p01")
    write_store(old, "dedust_grid50", [dict(seed=7)], port="dual", setup={"pixel_count": 50}, geom="p01")
    write_store(old, "dedust_ctrl", [dict(seed=8)], port="dual", setup={"timeout": 1200, "keep_project": True, "diag_bridge_w": 0.075}, geom="p01")
    write_harvest(old, "harvest_dual", 2, "dual")
    write_harvest(old, "harvest_single", 2, "single")
    got = {s: li.map_profile(old, s) for s in li.scan_stores(old, ["dedust_*", "handoff_*", "harvest_*"])}
    assert got["dedust_smp001a"] == ("dual_p01_db075", None)
    assert got["dedust_old"] == ("dual_p00", None)
    assert got["dedust_p01plain"] == ("dual_p01", None)
    assert got["dedust_s01"] == ("single_p00", None)
    assert got["handoff_ant_db100"] == ("single_db100", None)
    assert got["dedust_ctrl"] == ("dual_p01_db075", None), "timeout／keep_project 是控制鍵，不影響映射"
    assert got["dedust_r60slot"][0] is None and "slot_spec" in got["dedust_r60slot"][1]
    assert got["dedust_grid50"][0] is None and "pixel_count" in got["dedust_grid50"][1]
    assert got["harvest_dual"] == ("harvest_dual", None) and got["harvest_single"] == ("harvest_single", None)
    assert li.map_profile(old, "dedust_r60slot", overrides={"dedust_r60slot": "dual_p01"}) == ("dual_p01", None)
    assert "dedust_smp001a_input" not in got, "_input 夾不是 store"


# ── join 與欄位 ─────────────────────────────────────────────────────────────
def test_join_by_pattern_bytes_and_fields(old, out):
    ids = write_store(old, "dedust_smp002a", [dict(seed=11, arm="L", parent="x_999"), dict(seed=12, arm="d")],
                      port="dual", setup={"diag_bridge_w": 0.075}, geom="p01", dbw=0.075)
    reps, rc = li.import_legacy(old, out, stores=["dedust_*"])
    assert rc == 0 and len(reps) == 1 and reps[0].n_records == 2 and reps[0].n_join_failed == 0
    db = Database(out)
    recs = {r.note["legacy"]["id"]: r for r in db.view("dual_p01_db075").query()}
    r = recs[ids[0]]
    assert np.array_equal(r.bits, _pat(11)) and np.array_equal(r.response, _resp(11, 3))
    assert r.id == model.record_id(_pat(11), "dual_p01_db075") and r.sim_profile == "dual_p01_db075"
    assert r.strategy == "legacy:dedust_smp002a" and r.arm == "L" and r.kind == "sample" and r.status == "done"
    assert r.parent is None and r.note["legacy"]["parent_legacy"] == "x_999", "解析不到的親代留原字串"
    assert r.run == {"store": "dedust_smp002a", "machine": "216", "worker_ver": "legacy",
                     "profile_hash": r.run["profile_hash"], "time_s": 100.0}
    assert set(r.measure) == {"m1", "m2", "m3", "m4", "m5", "m6", "energy_max"} and isinstance(r.score, float)
    assert r.note["legacy"]["results"]["wm"] and r.note["legacy"]["manifest"]["port"] == "dual"
    assert recs[ids[1]].arm == "d" and r.tick is None and r.seed is None


def test_repeat_store_assigns_y_by_wm_match_and_suffixes_store(old, out):
    write_store(old, "dedust_rep", [dict(id="r00_rep", seed=21, kind="repeat", y_seed=21),
                                    dict(id="r01_rep", seed=21, kind="repeat", y_seed=22),
                                    dict(id="r02_rep", seed=21, kind="repeat", y_seed=21)],
                port="dual", setup={"diag_bridge_w": 0.075}, geom="p01", dbw=0.075)
    reps, rc = li.import_legacy(old, out, stores=["dedust_rep"])
    assert rc == 0 and reps[0].n_records == 3
    recs = sorted(Database(out).view("dual_p01_db075").query(), key=lambda r: r.note["legacy"]["id"])
    assert [r.kind for r in recs] == ["repeat"] * 3 and len({r.id for r in recs}) == 1
    assert np.array_equal(recs[1].response, _resp(22, 3)), "同 pattern 兩種 y：靠 wm 欄對回去"
    assert np.array_equal(recs[0].response, _resp(21, 3))
    assert [r.run["store"] for r in recs] == ["dedust_rep", "dedust_rep+r01_rep", "dedust_rep+r02_rep"]


def test_error_rows_skipped_by_default_include_errors_flag(old, out):
    write_store(old, "dedust_e", [dict(seed=31), dict(seed=32, error=True)], port="dual", geom="p01",
                setup={"diag_bridge_w": 0.075}, dbw=0.075)
    reps, _ = li.import_legacy(old, out, stores=["dedust_e"])
    assert reps[0].n_records == 1 and reps[0].n_errors == 1
    reps, _ = li.import_legacy(old, out, stores=["dedust_e"], include_errors=True, force=True)
    recs = Database(out).view("dual_p01_db075").query(status="error")
    assert len(recs) == 1 and recs[0].response is None and recs[0].note["error"] == "COM 例外"


def test_measure_recomputed_not_copied_and_verify_catches_mismatch_exit_2(old, out):
    write_store(old, "dedust_v", [dict(seed=41), dict(seed=42, wm_override=99.0)], port="dual", geom="p01",
                setup={"diag_bridge_w": 0.075}, dbw=0.075)
    reps, rc = li.import_legacy(old, out, stores=["dedust_v"], verify=True)
    assert rc == 2 and reps[0].n_verify_fail == 1 and reps[0].n_records == 2
    recs = {r.note["legacy"]["id"]: r for r in Database(out).view("dual_p01_db075").query()}
    bad = recs["dedust_v_001"]
    assert bad.measure["m1"] != 99.0 and bad.measure["m1"] == pytest.approx(
        am.worst_margin_dual(_resp(42, 3), DUAL, am.DUAL_R1_TARGETS)[1]["m1"])
    write_store(old, "dedust_ok", [dict(seed=43)], port="single")
    reps, rc = li.import_legacy(old, out, stores=["dedust_ok"], verify=True)
    assert rc == 0 and reps[0].n_verify_fail == 0


def test_parent_resolved_across_stores_ambiguous_none(old, out):
    a = write_store(old, "dedust_a", [dict(seed=51)], port="dual", geom="p01", setup={"diag_bridge_w": 0.075}, dbw=0.075)
    write_store(old, "dedust_b", [dict(seed=52, parent=a[0]), dict(seed=53, parent="dup"), dict(id="dup", seed=54)],
                port="dual", geom="p01", setup={"diag_bridge_w": 0.075}, dbw=0.075)
    write_store(old, "dedust_c", [dict(id="dup", seed=55)], port="dual", geom="p01", setup={"diag_bridge_w": 0.075}, dbw=0.075)
    li.import_legacy(old, out, stores=["dedust_*"])
    recs = {r.note["legacy"]["id"]: r for r in Database(out).view("dual_p01_db075").query()}
    assert recs["dedust_b_000"].parent == model.record_id(_pat(51), "dual_p01_db075"), "跨 store 解析親代"
    assert recs["dedust_b_001"].parent is None and recs["dedust_b_001"].note["legacy"]["parent_legacy"] == "dup", "歧義 → None"


def test_idempotent_rerun_no_dupes_changed_mtime_reimports_force(old, out):
    write_store(old, "dedust_i", [dict(seed=61), dict(seed=62)], port="dual", geom="p01", setup={"diag_bridge_w": 0.075}, dbw=0.075)
    reps, _ = li.import_legacy(old, out, stores=["dedust_i"])
    n_files = len(list(paths.db_dir(out, "dual_p01_db075").glob("*.npz")))
    reps2, _ = li.import_legacy(old, out, stores=["dedust_i"])
    assert reps2[0].skipped is True and len(list(paths.db_dir(out, "dual_p01_db075").glob("*.npz"))) == n_files
    imported = json.loads((paths.db_dir(out, "dual_p01_db075") / "_imported.json").read_text(encoding="utf-8"))
    assert "dedust_i" in imported and imported["dedust_i"]["n_records"] == 2
    res = old / "dedust_i" / "results.json"
    os.utime(res, (1_900_000_000, 1_900_000_000))
    reps3, _ = li.import_legacy(old, out, stores=["dedust_i"])
    assert reps3[0].skipped is False and reps3[0].n_records == 0, "重跑：檔已存在 → add 回 False，不重複"
    reps4, _ = li.import_legacy(old, out, stores=["dedust_i"], force=True)
    assert reps4[0].skipped is False


def test_harvest_imports_without_manifest(old, out):
    write_harvest(old, "harvest_dual", 3, "dual")
    reps, rc = li.import_legacy(old, out, stores=["harvest_dual"])
    assert rc == 0 and reps[0].n_records == 3
    recs = Database(out).view("harvest_dual").query()
    assert all(r.strategy == "legacy:harvest_dual" and r.arm is None and r.parent is None and r.kind == "sample" for r in recs)
    assert all(r.run["machine"] is None and r.run["time_s"] is None for r in recs)


def test_radiation_goes_to_extra(old, out):
    write_store(old, "dedust_s", [dict(seed=71)], port="single", rad=True)
    li.import_legacy(old, out, stores=["dedust_s"])
    r = Database(out).view("single_p00").query()[0]
    assert r.extra["radiation"]["theta"] == [0.0, 1.0, 2.0] and r.extra["radiation"]["phi0"] == [1.0, 1.0, 1.0]
    li.import_legacy(old, out, stores=["dedust_s"], force=True, no_rad=True)


def test_source_tree_untouched_and_dry_run_writes_nothing(old, out):
    write_store(old, "dedust_t", [dict(seed=81), dict(seed=82)], port="dual", geom="p01", setup={"diag_bridge_w": 0.075}, dbw=0.075)
    write_store(old, "dedust_bad", [dict(seed=83)], port="dual", setup={"slot_spec": [[0, 1]]}, geom="p01")
    snap = {p: p.stat().st_mtime_ns for p in old.rglob("*") if p.is_file()}
    reps, rc = li.import_legacy(old, out, stores=["dedust_*"], dry_run=True)
    assert rc == 0 and not list((out / "db").rglob("*.npz")) and not list((out / "db").rglob("_imported.json"))
    by = {r.store: r for r in reps}
    assert by["dedust_t"].profile == "dual_p01_db075" and by["dedust_t"].n_manifest == 2
    assert by["dedust_bad"].profile is None and "slot_spec" in by["dedust_bad"].reason
    li.import_legacy(old, out, stores=["dedust_*"])
    assert {p: p.stat().st_mtime_ns for p in old.rglob("*") if p.is_file()} == snap, "舊樹只讀"


def test_cli_import_legacy(old, out, capsys):
    write_store(old, "dedust_cli", [dict(seed=91)], port="dual", geom="p01", setup={"diag_bridge_w": 0.075}, dbw=0.075)
    rc = cli.main(["import-legacy", "--root", str(old), "--out", str(out), "--stores", "dedust_*", "--verify"])
    assert rc == 0
    assert "dedust_cli" in capsys.readouterr().out
    assert len(Database(out).view("dual_p01_db075").query()) == 1
    assert Path(paths.db_dir(out, "dual_p01_db075") / "_imported.json").exists()
