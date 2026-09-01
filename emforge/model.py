"""emforge/model.py — 契約資料類別：Profile／Spec／Proposal／Context／Record／Job，與去重鍵 `record_id`。

這裡是「schema」——舊系統完全沒有（全靠慣例、真相散在 docstring，見 docs/incidents.md 技術債）。
JSON 鍵一律等於欄位名（跨邊界不改名）。陣列用 numpy：pattern `bool[H,W]`、response `float32[n_labels, n_points]`。
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import paths
from .fs import sha1_hex

STATUS_QUEUED, STATUS_RUNNING, STATUS_DONE, STATUS_ERROR = "queued", "running", "done", "error"
STATUSES = (STATUS_QUEUED, STATUS_RUNNING, STATUS_DONE, STATUS_ERROR)
KIND_SAMPLE, KIND_REPEAT = "sample", "repeat"
KINDS = (KIND_SAMPLE, KIND_REPEAT)
#? 保留 arm：零演算法對照臂。report 只在 blind 樣本夠多時才印「勝過」（D8）。
ARM_BLIND = "blind"


# ── 小工具 ──────────────────────────────────────────────────────────────────
def canonical_json(obj) -> str:
    """鍵序無關、緊湊、不轉義中文——hash 用。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def pack_bits(bits) -> np.ndarray:
    """bool[H,W]（或 0/1 float）→ packbits uint8[ceil(HW/8)]。"""
    b = np.asarray(bits)
    if b.dtype != bool:
        b = b > 0.5
    return np.packbits(b.reshape(-1))


def unpack_bits(packed, shape) -> np.ndarray:
    n = int(np.prod(shape))
    return np.unpackbits(np.asarray(packed, np.uint8))[:n].astype(bool).reshape(tuple(shape))


def record_id(bits, sim_profile: str) -> str:
    """去重鍵：sha1(packbits(bits) + profile 名)[:16]。同 bits 不同 profile ＝ 不同設計（D6）。"""
    return sha1_hex(pack_bits(bits).tobytes(), sim_profile.encode("utf-8"))[:16]


def _check_name(kind: str, name: str) -> None:
    if not paths.is_valid_name(name):
        raise ValueError(f"{kind} 名 {name!r} 不合規（^[a-z][a-z0-9_]*$，見 docs/naming.md）")


# ── Profile（模擬庫成員） ────────────────────────────────────────────────────
@dataclass(frozen=True, eq=False)
class Profile:
    """一種模擬的全部定義＝儀器。append-only：改內容＝新名字（era ≡ profile 名）。"""
    name: str
    simulator: str          # "module.path:ClassName"，實作 Simulator 協定
    geom_ver: str           # 幾何底板版本；worker 開模擬器前與模擬器宣告比對（可為單邊）
    kwargs: dict            # 模擬器建構參數（原名直傳）
    shape: tuple
    labels: tuple
    n_points: int
    fixed_on: np.ndarray    # 饋墊等必為金屬的像素（生成端約束的事實來源）
    measure: str            # 凍結量測尺的名字
    spec: str               # 預設評估器（可換）
    timeout_s: int          # 單筆看門狗；只進看門狗、不進模擬器
    retired: bool = False

    def __post_init__(self):
        _check_name("profile", self.name)
        _check_name("spec", self.spec)
        _check_name("measure", self.measure)
        shape = tuple(int(x) for x in self.shape)
        fixed_on = np.asarray(self.fixed_on, bool)
        if fixed_on.shape != shape:
            raise ValueError(f"fixed_on 形狀 {fixed_on.shape} ≠ shape {shape}")
        object.__setattr__(self, "shape", shape)
        object.__setattr__(self, "labels", tuple(self.labels))
        object.__setattr__(self, "kwargs", dict(self.kwargs))
        object.__setattr__(self, "fixed_on", fixed_on)

    @property
    def profile_hash(self) -> str:
        """儀器指紋：simulator＋geom_ver＋kwargs＋measure。名字／spec／逾時／退役都不算。"""
        return sha1_hex(canonical_json([self.simulator, self.geom_ver, self.kwargs, self.measure]).encode("utf-8"))[:12]


# ── Spec（評估器，可換） ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class Spec:
    """score = min(measure[axis] + offset)。換 spec ＝ 註冊新名 → rescore → 新榜（D1／D3）。"""
    name: str
    labels: tuple
    measure: str
    axes: tuple
    offsets: tuple

    def __post_init__(self):
        _check_name("spec", self.name)
        if len(self.axes) != len(self.offsets):
            raise ValueError(f"axes {len(self.axes)} 與 offsets {len(self.offsets)} 長度不同")
        object.__setattr__(self, "labels", tuple(self.labels))
        object.__setattr__(self, "axes", tuple(self.axes))
        object.__setattr__(self, "offsets", tuple(float(o) for o in self.offsets))

    def score(self, measure: dict) -> float | None:
        """缺軸或 NaN → None（不猜、不入榜）。"""
        vals = []
        for a, o in zip(self.axes, self.offsets):
            v = measure.get(a)
            if v is None or float(v) != float(v):
                return None
            vals.append(float(v) + o)
        return min(vals)


# ── Proposal（策略唯一的輸出） ───────────────────────────────────────────────
class ProposalError(ValueError):
    """策略的提案不合契約。"""


@dataclass(frozen=True, eq=False)
class Proposal:
    pattern: np.ndarray
    parent: str | None = None
    arm: str | None = None
    note: dict = field(default_factory=dict)

    KEYS = frozenset({"pattern", "parent", "arm", "note"})

    @staticmethod
    def from_dict(d) -> "Proposal":
        """嚴格鍵集：多出 kind／sim_profile／score 等 runtime 欄位一律拒（D7：策略碰不到這些）。"""
        if isinstance(d, Proposal):
            return d
        if not isinstance(d, dict):
            raise ProposalError(f"proposal 必須是 dict 或 Proposal，得到 {type(d).__name__}")
        extra = set(d) - Proposal.KEYS
        if extra:
            raise ProposalError(f"forbidden_key: {sorted(extra)}——策略只能提 pattern/parent/arm/note")
        if "pattern" not in d:
            raise ProposalError("missing pattern")
        return Proposal(pattern=np.asarray(d["pattern"]), parent=d.get("parent"), arm=d.get("arm"),
                        note=dict(d.get("note") or {}))


# ── Context（系統給策略的六＋一樣東西） ──────────────────────────────────────
@dataclass
class Context:
    db: object              # View（唯讀）
    profile: Profile
    budget: int
    rng: np.random.Generator
    workdir: Path
    tick: int
    params: dict = field(default_factory=dict)   # strategies.yaml 的 params:


# ── Record（資料庫的一筆） ───────────────────────────────────────────────────
@dataclass(eq=False)
class Record:
    id: str
    sim_profile: str
    bits: np.ndarray
    response: np.ndarray | None
    measure: dict
    score: float | None
    status: str
    strategy: str
    arm: str | None
    parent: str | None
    tick: int | None
    seed: int | None
    note: dict
    kind: str
    run: dict               # {store, machine, worker_ver, profile_hash, time_s}
    extra: dict = field(default_factory=dict)   # 儀器側通道（如 radiation）；永不進 measure/score/report

    META_FIELDS = ("id", "sim_profile", "measure", "score", "status", "strategy", "arm", "parent", "tick", "seed",
                   "note", "kind", "run", "extra")

    def meta(self) -> dict:
        """純 JSON 的部分（陣列另存）。"""
        return {k: getattr(self, k) for k in Record.META_FIELDS}

    @staticmethod
    def from_meta(meta: dict, bits, response) -> "Record":
        return Record(bits=np.asarray(bits, bool),
                      response=None if response is None else np.asarray(response, np.float32),
                      **{k: meta[k] for k in Record.META_FIELDS})


# ── Job（佇列的一筆） ────────────────────────────────────────────────────────
@dataclass
class Job:
    store: str
    sim_profile: str
    profile_hash: str
    prio: int
    n: int
    machine: str | None = None      # 釘選機器 tag；None＝任一台
    origin: str = "runtime"         # 保留：之後 cli:deliver 等會用
    by: str = ""
    at: str = ""
    extra: dict = field(default_factory=dict)   # 不認得的鍵原樣帶著走

    FIELDS = ("store", "sim_profile", "profile_hash", "prio", "n", "machine", "origin", "by", "at")

    @staticmethod
    def from_dict(d: dict) -> "Job":
        known = {k: d[k] for k in Job.FIELDS if k in d}
        extra = {k: v for k, v in d.items() if k not in Job.FIELDS}
        return Job(**known, extra=extra)

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in Job.FIELDS}
        d.update(self.extra)
        return d
