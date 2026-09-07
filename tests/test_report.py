# -*- coding: utf-8 -*-
"""tests/test_report.py — `emforge/report.py`：每策略 n／best／命中率／P(勝 blind)／dup_dropped；K_MIN 閘；跨 profile 拒。

D8：對照臂不強制，但 blind 樣本不夠時**只印數字、不印比較**；I-10：同一 store 兩種 worker_ver 要警告。
"""
import numpy as np
import pytest

from emforge import db as dbm
from emforge import events, model, paths, report, testing

P = testing.FAKE_PROFILE


def _rec(seed, strategy, arm, score, store="s", worker_ver="v1", profile=P.name, kind="sample"):
    b = np.random.default_rng(seed).random(P.shape) > 0.5
    b[P.fixed_on] = True
    return model.Record(id=model.record_id(b, profile), sim_profile=profile, bits=b, response=np.zeros((2, 17), np.float32),
                        measure={"m1": score}, score=score, status="done", strategy=strategy, arm=arm, parent=None,
                        tick=1, seed=seed, note={}, kind=kind,
                        run={"store": store, "machine": "216", "worker_ver": worker_ver, "profile_hash": "h", "time_s": 1.0})


def _seed(root, n_blind, n_strat):
    testing.make_fake_root(root)
    d = dbm.Database(root)
    for i in range(n_blind):
        d.add(_rec(100 + i, "blind", "blind", -5.0 - i * 0.1, store="b1"))
    for i in range(n_strat):
        d.add(_rec(200 + i, "top_k_flip", None, -1.0 + i * 0.1, store="t1"))
    return d


def test_p_beats_blind_one_when_strictly_better_half_when_identical():
    out = report.p_beats_blind([1.0, 2.0, 3.0], [-1.0, -2.0], n_boot=200, seed=0)
    assert out["p"] == 1.0 and out["lo"] == 1.0 and out["hi"] == 1.0
    same = report.p_beats_blind([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], n_boot=200, seed=0)
    assert same["p"] == 0.5
    assert report.p_beats_blind([], [1.0]) is None and report.p_beats_blind([1.0], []) is None


def test_p_beats_blind_same_seed_equal_ci_bounds_valid():
    a = report.p_beats_blind([0.0, 1.0, 2.0], [0.5, 1.5], n_boot=300, seed=7)
    b = report.p_beats_blind([0.0, 1.0, 2.0], [0.5, 1.5], n_boot=300, seed=7)
    assert a == b and 0 <= a["lo"] <= a["p"] <= a["hi"] <= 1


def test_report_numbers_only_when_blind_below_k_min(root):
    """D8：blind n=2 < k_min=3 → 只印數字，沒有「勝過」欄。"""
    _seed(root, n_blind=2, n_strat=4)
    text = report.report(root, [P.name], k_min=3)
    assert "只印數字" in text and "P(勝 blind)" not in text
    assert "top_k_flip" in text and "blind" in text


def test_report_prints_comparison_when_blind_enough(root):
    _seed(root, n_blind=5, n_strat=4)
    rows = report.strategy_rows(dbm.Database(root), P.name, k_min=3)
    by = {r["strategy"]: r for r in rows}
    assert by["top_k_flip"]["n"] == 4 and by["top_k_flip"]["best"] == pytest.approx(-0.7)
    assert by["top_k_flip"]["p_beats_blind"] == 1.0 and by["blind"]["p_beats_blind"] is None
    assert by["top_k_flip"]["hit_rate"] == 0.0
    text = report.report(root, [P.name], k_min=3)
    assert "P(勝 blind)" in text and "1.00" in text


def test_report_refuses_cross_profile_without_flag_warns_with_flag(root):
    _seed(root, 1, 1)
    with pytest.raises(report.CrossProfileRefused):
        report.report(root, [P.name, "other_p"], k_min=1)
    text = report.report(root, [P.name, "other_p"], k_min=1, cross_profile=True)
    assert "⚠" in text and "跨 profile" in text


def test_report_counts_dup_dropped_from_events(root):
    _seed(root, 1, 1)
    ev = paths.events_jsonl(P.name)
    events.emit(root, ev, "proposals_validated", name="top_k_flip", tick=1, n_in=10, n_dup=3, n_out=7)
    events.emit(root, ev, "proposals_validated", name="top_k_flip", tick=2, n_in=10, n_dup=4, n_out=6)
    events.emit(root, ev, "proposals_validated", name="blind", tick=1, n_in=5, n_dup=0, n_out=5)
    rows = {r["strategy"]: r for r in report.strategy_rows(dbm.Database(root), P.name, k_min=1)}
    assert rows["top_k_flip"]["dup_dropped"] == 7 and rows["blind"]["dup_dropped"] == 0


def test_report_excludes_cli_prefixed_and_notarize_from_comparison(root):
    d = _seed(root, 3, 2)
    d.add(_rec(300, "notarize", None, 5.0, store="n1"))
    d.add(_rec(301, "cli:smoke", None, 6.0, store="sm1"))
    rows = report.strategy_rows(d, P.name, k_min=1)
    names = [r["strategy"] for r in rows]
    assert "notarize" in names and "cli:smoke" in names, "還是列出來（數字），只是不參與比較"
    by = {r["strategy"]: r for r in rows}
    assert by["notarize"]["p_beats_blind"] is None and by["cli:smoke"]["p_beats_blind"] is None
    assert by["top_k_flip"]["p_beats_blind"] is not None


def test_report_warns_when_store_has_two_worker_vers(root):
    """I-10：同一 store 出現兩種 worker_ver＝有人只 pull 沒重啟／中途換代——報表要說。"""
    d = _seed(root, 2, 1)
    d.add(_rec(400, "top_k_flip", None, -0.5, store="t1", worker_ver="v2"))
    warns = report.worker_ver_warnings(d, P.name)
    assert warns and "t1" in warns[0] and "v1" in warns[0] and "v2" in warns[0]
    assert "⚠" in report.report(root, [P.name], k_min=1)


def test_report_blind_reference_excludes_notarize_repeats(root):
    """檢查 #3（2026-09-07）：公證重測沿用 arm=blind、kind=repeat——同一片的三次重複量測不是三個獨立 blind 樣本：
    不進參考分佈、不灌 blind_n（k_min 閘）；P(勝 blind) 公證前後不變。notarize 列照列、仍不可比。"""
    testing.make_fake_root(root)
    d = dbm.Database(root)
    for i in range(25):
        d.add(_rec(100 + i, "blind", "blind", -float(i), store="b1"))              # blind 冠軍＝seed 100、score 0.0
    for i in range(25):
        d.add(_rec(200 + i, "top_k_flip", None, -float(i) + 0.5, store="t1"))
    before = {r["strategy"]: r for r in report.strategy_rows(d, P.name, k_min=20)}
    assert report.blind_count(d, P.name) == 25 and 0.4 < before["top_k_flip"]["p_beats_blind"] < 0.6, "分佈要重疊測試才有意義"
    for n in range(3):
        d.add(_rec(100, "notarize", "blind", 0.0, store=f"nz{n}", kind="repeat"))
    after = {r["strategy"]: r for r in report.strategy_rows(d, P.name, k_min=20)}
    assert report.blind_count(d, P.name) == 25, "重測不灌 blind_n"
    assert after["top_k_flip"]["p_beats_blind"] == before["top_k_flip"]["p_beats_blind"], "重測不改 P"
    assert after["notarize"]["n_done"] == 3 and after["notarize"]["comparable"] is False

