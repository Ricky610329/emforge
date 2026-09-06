"""emforge/legacy/antenna_import.py — 舊 NAS（DATASET_PATH）→ db/<profile>/：profile 自動映射、pattern-bytes join、量測重算、--verify。

舊樹：`<store>_input/manifest.json + <id>.pt`（float32 25×25）、`<store>/results.json`（id → entry）、
`<store>/<sha>.pt`（SampleStore 的 (x, y)，內容 hash 命名、無法從 id 反推）、`<store>/hfss_setup.json`、`<store>/rad/<id>.pt`、
`jobs_state/<store>.done`；`harvest_*` 只有 tuple 檔。
規則：量測**重算**不抄舊值（--verify 對回舊 wm 欄＝新尺＝舊尺的實證）；error 列預設跳過；幾何／網格變體（slot_spec、
pixel_count≠25…）不映射（舊規則「永不入鍋」）；`_imported.json` 記 store 簽名，重跑跳過未變的；**只讀舊樹**。
"""
import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .. import fs, model, paths, specs
from .. import profiles as core
from ..adapters.antenna import profiles as aprof
from ..db import Database
from ..depot import open_depot

CONTROL_KEYS = {"timeout", "keep_project"}          # 舊白名單裡不進模擬器的鍵，不影響映射
REPEAT_KINDS = {"repeat", "notarize"}
PARENT_KEYS = ("parent", "parent_id", "source_id", "base_id")
VERIFY_TOL = 0.006                                   # 舊 wm 欄 2 位小數


@dataclass
class StoreReport:
    store: str
    profile: str | None = None
    reason: str | None = None
    skipped: bool = False
    n_manifest: int = 0
    n_pt: int = 0
    n_records: int = 0
    n_errors: int = 0
    n_join_failed: int = 0
    n_verify_fail: int = 0


# ── 讀舊樹 ──────────────────────────────────────────────────────────────────
def _torch():
    import torch
    return torch


def _load_tensor(path: Path):
    return _torch().load(str(path), weights_only=True)


def scan_stores(old_root, patterns: list) -> list:
    old = Path(old_root)
    names = sorted(d.name for d in old.iterdir() if d.is_dir() and not d.name.endswith("_input") and d.name != "jobs_state")
    return [n for n in names if any(fnmatch.fnmatch(n, p) for p in patterns)]


def _manifest(old: Path, store: str):
    p = old / f"{store}_input" / "manifest.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _results(old: Path, store: str) -> dict:
    return fs.read_json(old / store / "results.json", default={})


def _setup(old: Path, store: str) -> dict:
    return fs.read_json(old / store / "hfss_setup.json", default={})


def _machine(old: Path, store: str):
    d = fs.read_json(old / "jobs_state" / f"{store}.done", default=None)
    m = (d or {}).get("machine")
    return m.rsplit(".", 1)[-1] if m else None


def _input_pattern(old: Path, store: str, legacy_id: str):
    p = old / f"{store}_input" / f"{legacy_id}.pt"
    return np.asarray(_load_tensor(p).numpy()) > 0.5 if p.exists() else None


def _tuples(old: Path, store: str) -> dict:
    """{packbits(x).tobytes(): [y, …]}（同 pattern 多次量測）。"""
    by = {}
    for p in sorted((old / store).glob("*.pt")):
        x, y = _load_tensor(p)
        bits = np.asarray(x.numpy()) > 0.5
        by.setdefault(model.pack_bits(bits).tobytes(), []).append(np.asarray(y.numpy(), np.float32))
    return by


def _radiation(old: Path, store: str, legacy_id: str) -> dict:
    p = old / store / "rad" / f"{legacy_id}.pt"
    if not p.exists():
        return {}
    d = _load_tensor(p)
    if not isinstance(d, dict) or "theta" not in d:
        return {}
    return {"radiation": {k: [float(v) for v in np.asarray(d[k]).reshape(-1)] for k in ("theta", "phi0", "phi90") if k in d}}


# ── 映射 ────────────────────────────────────────────────────────────────────
def detect_domain(old_root, store: str) -> str:
    man = _manifest(Path(old_root), store)
    if man is None:
        return "dual" if "dual" in store else "single"
    return "dual" if any(m.get("port") == "dual" for m in man) else "single"


def map_profile(old_root, store: str, overrides: dict | None = None) -> tuple:
    """回 (profile 名, None) 或 (None, 原因)。"""
    old = Path(old_root)
    if overrides and store in overrides:
        return overrides[store], None
    if store.startswith("harvest_dual"):
        return "harvest_dual", None
    if store.startswith("harvest_single"):
        return "harvest_single", None
    domain = detect_domain(old, store)
    #? 值等於預設的求解鍵不是變體（舊 dedust_r*ms0 那類把預設明寫出來；review 砍掉的 antenna_import:118）
    defaults = {**aprof.HFSS_DEFAULTS, "pixel_count": 25, "sweep_type": "Fast" if domain == "dual" else "Interpolating"}
    setup = {k: v for k, v in _setup(old, store).items()
             if k not in CONTROL_KEYS and not (k in defaults and v == defaults[k])}
    dbw = setup.pop("diag_bridge_w", None)
    if setup:
        return None, f"unmapped: 幾何／網格變體 hfss_setup 鍵 {sorted(setup)}（舊規則：永不入鍋）"
    if domain == "dual":
        geoms = {e.get("geom", "p00") for e in _results(old, store).values() if isinstance(e, dict) and "wm" in e}
        if len(geoms) > 1:
            return None, f"unmapped: store 內 geom 不一致 {sorted(geoms)}"
        geom = geoms.pop() if geoms else "p00"
        if dbw is None:
            return ("dual_p00" if geom == "p00" else "dual_p01"), None
        if geom == "p01" and abs(float(dbw) - 0.075) < 1e-9:
            return "dual_p01_db075", None
        return None, f"unmapped: dual geom={geom} diag_bridge_w={dbw} 沒有對應 profile"
    if dbw is None:
        return "single_p00", None
    if abs(float(dbw) - 0.1) < 1e-9:
        return "single_db100", None
    return None, f"unmapped: single diag_bridge_w={dbw} 沒有對應 profile"


# ── 建 Record ───────────────────────────────────────────────────────────────
def _measure(prof, y):
    response = np.asarray(y, np.float32)
    measure = specs.measure(prof.measure, response, prof.labels)
    return response, measure, specs.score(prof.spec, measure)


def _cols(prof, y) -> list:
    """舊 results.json 的 wm 欄前 n_labels 個：每 label 的主 margin。"""
    m = specs.measure(prof.measure, y, prof.labels)
    if "m1" in m:
        return [m["m1"], min(m["m3"], m["m4"]), m["m2"]]
    return [m[l] for l in prof.labels]


def _pick_y(prof, ys: list, entry: dict):
    if len(ys) == 1:
        return ys[0]
    want = [round(float(v), 2) for v in entry.get("wm", [])[:len(prof.labels)]]
    hits = [y for y in ys if [round(c, 2) for c in _cols(prof, y)] == want]
    return hits[0] if hits else None


def _record(prof, bits, y, *, legacy_id, store, run_store, manifest: dict, entry: dict, machine, resolver: dict, extra: dict):
    if y is None:
        response, measure, score, status = None, {}, None, model.STATUS_ERROR
    else:
        response, measure, score = _measure(prof, y)
        status = model.STATUS_DONE
    parent_legacy = next((manifest[k] for k in PARENT_KEYS if manifest.get(k) is not None), None)
    legacy = {"id": legacy_id, "kind": manifest.get("kind"), "store": store,
              "manifest": {k: v for k, v in manifest.items() if k != "id"}, "results": entry}
    if parent_legacy is not None:
        legacy["parent_legacy"] = parent_legacy
    note = {"legacy": legacy}
    if y is None:
        note["error"] = (entry or {}).get("error", "unknown")
    seed = manifest.get("seed") if isinstance(manifest.get("seed"), int) else None
    kind = model.KIND_REPEAT if manifest.get("kind") in REPEAT_KINDS else model.KIND_SAMPLE
    return model.Record(id=model.record_id(bits, prof.name), sim_profile=prof.name, bits=bits, response=response,
                        measure=measure, score=score, status=status, strategy=f"legacy:{store}", arm=manifest.get("arm"),
                        parent=resolver.get(parent_legacy) if parent_legacy is not None else None, tick=None, seed=seed,
                        note=note, kind=kind,
                        run={"store": run_store, "machine": machine, "worker_ver": "legacy",
                             "profile_hash": prof.profile_hash, "time_s": (entry or {}).get("time_s")},
                        extra=extra)


def _harvest_record(prof, bits, y, *, store, file_name: str):
    response, measure, score = _measure(prof, y)
    return model.Record(id=model.record_id(bits, prof.name), sim_profile=prof.name, bits=bits, response=response,
                        measure=measure, score=score, status=model.STATUS_DONE, strategy=f"legacy:{store}", arm=None,
                        parent=None, tick=None, seed=None, note={"legacy": {"store": store, "file": file_name}},
                        kind=model.KIND_SAMPLE,
                        run={"store": store, "machine": None, "worker_ver": "legacy", "profile_hash": prof.profile_hash,
                             "time_s": None}, extra={})


# ── 匯入一個 store ──────────────────────────────────────────────────────────
@dataclass
class _Opts:
    include_errors: bool = False
    verify: bool = False
    dry_run: bool = False
    force: bool = False
    no_rad: bool = False


def _signature(old: Path, store: str) -> dict:
    return {"results_mtime": fs.mtime(old / store / "results.json"), "n_pt": len(list((old / store).glob("*.pt")))}


def _import_store(old: Path, db: Database, rep: StoreReport, resolver: dict, opts: _Opts) -> None:
    prof = core.get_profile(rep.profile)
    imported_key = paths.db_imported(prof.name)
    imported = db.depot.get_json(imported_key) or {}
    sig = _signature(old, rep.store)
    rep.n_pt = sig["n_pt"]
    prev = imported.get(rep.store)
    if prev and not opts.force and not opts.dry_run and all(prev.get(k) == sig[k] for k in sig):
        rep.skipped, rep.n_records = True, prev.get("n_records", 0)
        return
    man = _manifest(old, rep.store)
    records = _harvest_records(old, rep, prof) if man is None else _manifest_records(old, rep, prof, man, resolver, opts)
    if opts.dry_run:
        return
    for rec in records:
        if db.add(rec):
            rep.n_records += 1
    imported[rep.store] = {**sig, "n_records": rep.n_records, "n_errors": rep.n_errors, "n_join_failed": rep.n_join_failed,
                           "n_verify_fail": rep.n_verify_fail, "imported_at": model.now_iso()}
    db.depot.put_json(imported_key, imported)


def _harvest_records(old: Path, rep: StoreReport, prof) -> list:
    out = []
    for p in sorted((old / rep.store).glob("*.pt")):
        x, y = _load_tensor(p)
        out.append(_harvest_record(prof, np.asarray(x.numpy()) > 0.5, np.asarray(y.numpy(), np.float32),
                                   store=rep.store, file_name=p.name))
    rep.n_manifest = len(out)
    return out


def _manifest_records(old: Path, rep: StoreReport, prof, man: list, resolver: dict, opts: _Opts) -> list:
    results, by_key, machine = _results(old, rep.store), _tuples(old, rep.store), _machine(old, rep.store)
    rep.n_manifest = len(man)
    seen, out = {}, []
    for m in man:
        lid = m["id"]
        bits = _input_pattern(old, rep.store, lid)
        if bits is None:
            rep.n_join_failed += 1
            continue
        entry = results.get(lid)
        rid = model.record_id(bits, prof.name)
        run_store = rep.store if rid not in seen else f"{rep.store}+{lid}"
        seen[rid] = seen.get(rid, 0) + 1
        if not isinstance(entry, dict) or "wm" not in entry:
            rep.n_errors += 1
            if opts.include_errors:
                out.append(_record(prof, bits, None, legacy_id=lid, store=rep.store, run_store=run_store, manifest=m,
                                   entry=entry or {}, machine=machine, resolver=resolver, extra={}))
            continue
        y = _pick_y(prof, by_key.get(model.pack_bits(bits).tobytes(), []), entry)
        if y is None:
            rep.n_join_failed += 1
            continue
        if opts.verify and any(abs(a - float(b)) > VERIFY_TOL for a, b in zip(_cols(prof, y), entry["wm"])):
            rep.n_verify_fail += 1
        extra = {} if opts.no_rad else _radiation(old, rep.store, lid)
        out.append(_record(prof, bits, y, legacy_id=lid, store=rep.store, run_store=run_store, manifest=m, entry=entry,
                           machine=machine, resolver=resolver, extra=extra))
    return out


# ── 入口 ────────────────────────────────────────────────────────────────────
def import_legacy(old_root, out_depot, *, stores: list, profile: str = "auto", overrides: dict | None = None,
                  include_errors: bool = False, verify: bool = False, dry_run: bool = False, force: bool = False,
                  no_rad: bool = False, out=print) -> tuple:
    """回 (reports, rc)：rc 0；有 verify 不符 → 2。第一遍算映射與親代解析表，第二遍匯入。
    `old_root`＝舊 NAS 樹（本機路徑、只讀）；`out_depot`＝emforge 共享狀態（Depot／spec／路徑皆可）。"""
    old, depot = Path(old_root), open_depot(out_depot)
    aprof.register_all()
    opts = _Opts(include_errors, verify, dry_run, force, no_rad)
    plan, legacy_ids = [], {}
    for s in scan_stores(old, stores):
        prof_name, reason = map_profile(old, s, overrides)
        if profile != "auto" and prof_name != profile:
            continue
        rep = StoreReport(store=s, profile=prof_name, reason=reason)
        plan.append(rep)
        if prof_name is None:
            continue
        man = _manifest(old, s) or []
        rep.n_manifest = len(man)
        for m in man:
            bits = _input_pattern(old, s, m["id"])
            if bits is not None:
                legacy_ids.setdefault(m["id"], set()).add(model.record_id(bits, prof_name))
    resolver = {lid: next(iter(rids)) for lid, rids in legacy_ids.items() if len(rids) == 1}
    db = Database(depot)
    rc = 0
    for rep in plan:
        if rep.profile is None:
            out(f"✗ {rep.store}: {rep.reason}")
            continue
        _import_store(old, db, rep, resolver, opts)
        if rep.n_verify_fail:
            rc = 2
        flag = "skip" if rep.skipped else ("dry" if dry_run else "ok")
        out(f"{flag:4} {rep.store} → {rep.profile}: manifest={rep.n_manifest} pt={rep.n_pt} records={rep.n_records} "
            f"errors={rep.n_errors} join_failed={rep.n_join_failed} verify_fail={rep.n_verify_fail}")
    return plan, rc
