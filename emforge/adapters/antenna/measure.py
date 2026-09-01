"""emforge/adapters/antenna/measure.py — 凍結的 targets ＋ vendored numpy 版兩把尺。

為什麼 vendor 而不是每次呼叫 `antenna.losses`：量測凍結的意義就是「不隨舊 repo 的下一個 commit 漂」；runtime 在開發機算量測，
不必拉 torch/matplotlib；兩把尺加起來不到百行純算術。「兩份會漂」的風險由 tests/adapters/test_antenna_bound.py 的
500 筆逐位元 parity 接住（舊 repo 可 import 時就跑）。
targets 凍結成常數：呼叫時給的 targets 若與凍結值不同 → MeasureFrozen（改口徑＝註冊新 measure 名，不是改這裡）。
口徑出處：舊 repo `antenna/losses.py:661-694`（single）、`:703-820`（dual）；數值出處 `configs/dual_r1_eval.yaml`、`single_base.yaml`。
"""
from types import MappingProxyType

import numpy as np

DUAL_LABELS = ("S11", "S21", "S22")
SINGLE_LABELS = ("S11", "Gain")
N_POINTS = 17                       # 24–32 GHz，0.5 GHz 一點
MEASURE_DUAL_R1 = "antenna_dual_r1"
MEASURE_SINGLE_BASE = "antenna_single_base"


def _freeze(d: dict) -> MappingProxyType:
    return MappingProxyType({k: MappingProxyType(dict(v)) for k, v in d.items()})


#! 凍結（2026-08-10 判準定案；規格 v2 只動評分層的 offsets，不動這裡）
DUAL_R1_TARGETS = _freeze({
    "S11": {"side": -1.25, "center": -12.0, "width": (4, 2, 5, 2, 4)},
    "S21": {"side": -20.0, "center": -3.0, "width": (3, 0, 11, 0, 3)},
    "S22": {"side": -1.25, "center": -12.0, "width": (4, 2, 5, 2, 4)},
})
SINGLE_BASE_TARGETS = _freeze({
    "S11": {"side": 0.0, "center": -10.0, "width": (5, 0, 7, 0, 5), "method": "low"},
    "Gain": {"side": -19.0, "center": 4.0, "width": (5, 0, 7, 0, 5), "method": "high"},
})


class MeasureFrozen(Exception):
    """給進來的 targets 與凍結值不同——改口徑請註冊新 measure 名。"""


def _plain(t) -> dict:
    return {k: {kk: (list(vv) if isinstance(vv, (list, tuple)) else vv) for kk, vv in dict(v).items()} for k, v in t.items()}


def _check_frozen(targets, frozen) -> None:
    if _plain(targets) != _plain(frozen):
        raise MeasureFrozen("targets 與凍結值不同——量測口徑是凍結的；改口徑＝註冊新 measure 名")


# ── vendored：single ────────────────────────────────────────────────────────
def worst_margin(response, labels, targets) -> tuple:
    """single 尺：中央平台切片 `w0+w1 : w0+w1+w2`；low→center−max(band)、high→min(band)−center；wm=min。"""
    labels = list(labels)
    r = np.asarray(response, np.float32).reshape(len(labels), -1)
    margins = {}
    for i, label in enumerate(labels):
        t = targets[label]
        if "method" not in t:
            raise ValueError(f"worst_margin 只支援 single-port (method) target；{label} 無 'method'")
        w = list(t["width"])
        a, b = w[0] + w[1], w[0] + w[1] + w[2]
        if b > r.shape[1] or w[2] <= 0:
            raise ValueError(f"worst_margin: {label} 的中央平台切片 [{a}:{b}] 越界或為空（響應僅 {r.shape[1]} 點，width={w}）。請檢查 targets 的 width。")
        band = r[i][a:b]
        c = float(t["center"])
        margins[label] = (c - float(band.max())) if t["method"] == "low" else (float(band.min()) - c)
    return min(margins.values()), margins


# ── vendored：dual ──────────────────────────────────────────────────────────
def dual_target_curve(t, n_points: int, label: str) -> np.ndarray:
    w = list(t["width"])
    if len(w) != 5:
        raise ValueError(f"worst_margin_dual: {label} 的 width 需 5 段，得到 {len(w)} 段 ({w})。")
    side, center = float(t["side"]), float(t["center"])
    curve = np.concatenate([np.ones(w[0]) * side, np.linspace(side, center, w[1]), np.ones(w[2]) * center,
                            np.linspace(center, side, w[3]), np.ones(w[4]) * side])
    if len(curve) != n_points:
        raise ValueError(f"worst_margin_dual: {label} 的 width {w} 展開成 {len(curve)} 點，但響應有 {n_points} 點。")
    return curve


def worst_margin_dual(response, labels, targets) -> tuple:
    """dual 尺：頻點集合走 mask（curve==min／max），不用切片算術（斜邊端點會被漏）。回 (wm, per)。"""
    labels = list(labels)
    need = {"S11", "S21", "S22"}
    if not need.issubset(labels):
        raise ValueError(f"worst_margin_dual 需要 labels 含 {sorted(need)}，得到 {labels}。")
    r = np.asarray(response, np.float32).reshape(len(labels), -1)
    n_points = r.shape[1]
    curves, rows, sides, centers = {}, {}, {}, {}
    for label in ("S11", "S21", "S22"):
        t = targets[label]
        curves[label] = dual_target_curve(t, n_points, label)
        rows[label] = r[labels.index(label)]
        sides[label], centers[label] = float(t["side"]), float(t["center"])
    for label in ("S11", "S22"):                 #! 方向自證：寫反會靜默反號
        if not centers[label] < sides[label]:
            raise ValueError(f"worst_margin_dual: {label} 應為帶內壓低型 (center < side)，但 center={centers[label]}、side={sides[label]}。")
    if not centers["S21"] > sides["S21"]:
        raise ValueError(f"worst_margin_dual: S21 應為通帶抬高型 (center > side)，但 center={centers['S21']}、side={sides['S21']}。")

    def _lo(label):
        c = curves[label]
        return rows[label][c == c.min()]

    def _hi(label):
        c = curves[label]
        return rows[label][c == c.max()]

    per = {
        "m1": centers["S11"] - float(_lo("S11").max()),
        "m2": centers["S22"] - float(_lo("S22").max()),
        "m3": float(_hi("S21").min()) - centers["S21"],
        "m4": sides["S21"] - float(_lo("S21").max()),
        "m5": float(_hi("S11").min()) - sides["S11"],
        "m6": float(_hi("S22").min()) - sides["S22"],
    }
    wm = min(per["m1"], per["m2"], per["m3"], per["m4"])
    per["S11"], per["S22"], per["S21"] = per["m1"], per["m2"], min(per["m3"], per["m4"])
    return wm, per


def dual_energy_max(response) -> float:
    """能量自證：max over 頻點 of max(|S11|²+|S21|², |S22|²+|S21|²)（dB→線性），被動網路必 ≤ 1。列序固定 (S11,S21,S22)。"""
    r = np.asarray(response, np.float32).reshape(3, -1)
    p = np.power(np.float32(10.0), r / np.float32(10.0))
    return float(np.maximum(p[0] + p[1], p[2] + p[1]).max())


# ── 給註冊表的 measure 函式 ──────────────────────────────────────────────────
def measure_dual_r1(response, labels, targets) -> dict:
    _check_frozen(targets, DUAL_R1_TARGETS)
    _, per = worst_margin_dual(response, labels, targets)
    out = {k: per[k] for k in ("m1", "m2", "m3", "m4", "m5", "m6")}
    out["energy_max"] = dual_energy_max(np.asarray(response, np.float32).reshape(len(labels), -1)[[list(labels).index(l) for l in DUAL_LABELS]])
    return out


def measure_single_base(response, labels, targets) -> dict:
    _check_frozen(targets, SINGLE_BASE_TARGETS)
    _, per = worst_margin(response, labels, targets)
    return dict(per)
