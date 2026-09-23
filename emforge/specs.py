"""emforge/specs.py — 量測尺（measure）與評估器（spec）的註冊表。**append-only**：同名不同內容＝衝突，不是覆蓋。

兩層（architecture §8）：
- measure：原始響應 → 軸值 dict（m1..）。**凍結**——targets 在註冊時綁定，呼叫端不能塞別的；改口徑＝新名字。
- spec：軸值 → 一個分數（`Spec.score`）。可換；換 spec ＝ 註冊新名 → rescore → 新榜，零重量（D1／D3）。

分數只在 runtime 算（策略自評＝離線考，C-17 三度失敗）。
"""
from dataclasses import dataclass, asdict

import numpy as np

from . import paths
from .model import Spec, canonical_json


class RegistryConflict(Exception):
    """同名不同內容。append-only：改內容請換名字。"""


class UnknownMeasure(Exception):
    pass


class UnknownSpec(Exception):
    pass


class MeasureLabelsMismatch(Exception):
    """儀器給的 labels 與尺要的不同（I-6 型：跑之前就擋）。"""


@dataclass(frozen=True)
class Measure:
    name: str
    fn: object                  # fn(response: f32[n_labels, n_points], labels: tuple, targets: dict) -> dict[str, number]
    labels: tuple
    targets: dict

    def key(self) -> tuple:
        """內容身分：函式的 module+qualname（同一份程式碼重跑註冊要 no-op）＋labels＋targets。"""
        return (getattr(self.fn, "__module__", ""), getattr(self.fn, "__qualname__", repr(self.fn)),
                self.labels, canonical_json(self.targets))


_MEASURES: dict = {}
_SPECS: dict = {}


def register_measure(name: str, fn, *, labels, targets: dict) -> Measure:
    if not paths.is_valid_name(name):
        raise ValueError(f"measure 名 {name!r} 不合規（^[a-z][a-z0-9_]*$）")
    m = Measure(name, fn, tuple(labels), dict(targets))
    cur = _MEASURES.get(name)
    if cur is not None:
        if cur.key() != m.key():
            raise RegistryConflict(f"measure {name!r} 已註冊且內容不同——改口徑請換名字（凍結）")
        return cur
    _MEASURES[name] = m
    return m


def register_spec(spec: Spec) -> Spec:
    cur = _SPECS.get(spec.name)
    if cur is not None:
        if cur != spec:
            raise RegistryConflict(f"spec {spec.name!r} 已註冊且內容不同——換評估器請註冊新名")
        return cur
    _SPECS[spec.name] = spec
    return spec


def get_measure(name: str) -> Measure:
    try:
        return _MEASURES[name]
    except KeyError:
        raise UnknownMeasure(f"measure {name!r} 未註冊（已註冊：{measure_names()}）") from None


def get_spec(name: str) -> Spec:
    try:
        return _SPECS[name]
    except KeyError:
        raise UnknownSpec(f"spec {name!r} 未註冊（已註冊：{spec_names()}）") from None


def measure_names() -> list:
    return sorted(_MEASURES)


def spec_names() -> list:
    return sorted(_SPECS)


def clear_registry() -> None:
    """測試用。"""
    _MEASURES.clear()
    _SPECS.clear()


def measure(name: str, response, labels) -> dict:
    """用註冊的尺量一筆原始響應。targets 由註冊表綁定；回傳值一律 python float。"""
    m = get_measure(name)
    if tuple(labels) != m.labels:
        raise MeasureLabelsMismatch(f"measure {name!r} 要 labels {m.labels}，儀器給 {tuple(labels)}")
    out = m.fn(np.asarray(response, np.float32), m.labels, m.targets)
    return {k: float(v) for k, v in out.items()}


def score(spec_name: str, measured: dict) -> float | None:
    return get_spec(spec_name).score(measured)


def snapshot(name):
    return asdict(get_spec(name))


def frozen(name, definition=None):
    spec = Spec(**definition) if definition else get_spec(name)
    if spec.name != name:
        raise ValueError("spec 快照名稱不一致")
    return spec


PASSIVITY_KEY = "energy_max"      #? 量測函式可回報的自證欄位：max over 頻點 of Σ|S|²（被動網路必 ≤ 1）
PASSIVITY_TOL = 0.05              #? 數值噪音容差；超過＝這筆響應物理上不可能，是模擬壞了不是設計好（I-29）


def check_passivity(measure: dict) -> None:
    """量測帶 `energy_max` 且 > 1+容差 → ValueError（collect 記成 measure_failed）；沒有這欄位的尺不受影響。"""
    v = measure.get(PASSIVITY_KEY)
    if v is not None and not (float(v) <= 1.0 + PASSIVITY_TOL):
        raise ValueError(f"{PASSIVITY_KEY}={float(v):.3f} > {1.0 + PASSIVITY_TOL}：響應違反能量守恆（被動網路 Σ|S|² ≤ 1），拒收")
