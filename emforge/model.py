"""emforge/model.py — 契約資料類別：Profile／Spec／Proposal／Context／Record／Job，與去重鍵 `record_id`。

這裡是「schema」——舊系統完全沒有（全靠慣例、真相散在 docstring，見 docs/incidents.md 技術債）。
JSON 鍵一律等於欄位名（跨邊界不改名）。陣列用 numpy：pattern `bool[H,W]`、response `float32[n_labels, n_points]`。
"""
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from . import paths, scoring

STATUS_QUEUED, STATUS_RUNNING, STATUS_DONE, STATUS_ERROR = "queued", "running", "done", "error"
STATUSES = (STATUS_QUEUED, STATUS_RUNNING, STATUS_DONE, STATUS_ERROR)
KIND_SAMPLE, KIND_REPEAT = "sample", "repeat"
KINDS = (KIND_SAMPLE, KIND_REPEAT)
#? 保留 arm：零演算法對照臂。report 只在 blind 樣本夠多時才印「勝過」（D8）。
ARM_BLIND = "blind"


# ── 小工具 ──────────────────────────────────────────────────────────────────
#? 這兩個原本住在 fs.py，但它們與檔案系統無關（時間戳與雜湊都是 schema 的一部分：`at` 欄位、`record_id`／
#  `profile_hash`／榜的 `_checksum`）。M12b 搬進來，讓已抽象化的模組不必為了它們 import fs。
def now_iso() -> str:
    """本地時間 `YYYY-MM-DDTHH:MM:SS`（欄位名一律 `at`）。"""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def sha1_hex(*parts: bytes) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p)
    return h.hexdigest()


def _json_default(o):
    """canonical_json 的後備轉換：Mapping（含 MappingProxyType 凍結 targets）→ dict、numpy → python。"""
    if isinstance(o, Mapping):
        return dict(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, np.generic):
        return o.item()
    raise TypeError(f"canonical_json 不會序列化 {type(o).__name__}")


def canonical_json(obj) -> str:
    """鍵序無關、緊湊、不轉義中文——hash 用。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_json_default)


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

    aggregate: str = "min"
    weights: tuple = ()
    gates: tuple = ()

    def __post_init__(self):
        _check_name("spec", self.name)
        scoring.validate(self)
        for name in ("labels", "axes"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        for name in ("offsets", "weights"):
            object.__setattr__(self, name, tuple(float(v) for v in getattr(self, name)))
        object.__setattr__(self, "gates", tuple((a, op, float(v)) for a, op, v in self.gates))

    def score(self, measure: dict) -> float | None:
        """缺軸、非有限值或門檻未過 → None。"""
        return scoring.score(self, measure)

    def gated(self, measure: dict) -> bool:
        return not scoring.passes(self.gates, measure)


# ── Proposal（策略唯一的輸出） ───────────────────────────────────────────────
class ProposalError(ValueError):
    """策略的提案不合契約。"""


@dataclass(frozen=True, eq=False)
class Proposal:
    pattern: np.ndarray
    parent: str | None = None
    arm: str | None = None
    note: dict = field(default_factory=dict)

    tag: str | None = None
    run_id: str | None = None

    KEYS = frozenset({"pattern", "parent", "arm", "note", "tag", "run_id"})

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
                        note=dict(d.get("note") or {}), tag=d.get("tag"), run_id=d.get("run_id"))


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


# ── Simulator 協定（模擬庫成員要長的樣子） ───────────────────────────────────
@dataclass(frozen=True)
class SimResult:
    response: np.ndarray            # float32[n_labels, n_points]
    time_s: float
    extra: dict = field(default_factory=dict)   # 儀器側通道；核心不解讀，原樣進 Record.extra


class Simulator:
    """核心只認這五樣：`geom_ver`／`labels` 類別屬性 + open／simulate／kill／close。
    建構子簽名固定 `(*, workdir: str, profile: Profile)`；`geom_ver=None` 表示單邊宣告（不與 profile 比對）。
    這個基底類別只是文件：adapter 可以繼承，也可以純 duck typing。"""
    geom_ver: str | None = None
    labels: tuple = ()

    def __init__(self, *, workdir: str, profile: Profile):
        self.workdir, self.profile = workdir, profile

    def open(self) -> None:
        """一次昂貴連線（HFSS COM）。"""
        raise NotImplementedError

    def simulate(self, bits: np.ndarray) -> SimResult:
        """一筆進、一筆出；可被 kill() 中斷（看門狗）。"""
        raise NotImplementedError

    def kill(self) -> None:
        """OS 級強殺，可重入；看門狗逾時呼叫。"""
        raise NotImplementedError

    def close(self) -> None:
        """優雅關閉；失敗不拋。"""
        raise NotImplementedError


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

    tag: str | None = None
    run_id: str | None = None

    META_FIELDS = ("id", "sim_profile", "measure", "score", "status", "strategy", "arm", "parent", "tick", "seed",
                   "note", "kind", "run", "extra", "tag", "run_id")

    def meta(self) -> dict:
        """純 JSON 的部分（陣列另存）。"""
        return {k: getattr(self, k) for k in Record.META_FIELDS}

    @staticmethod
    def from_meta(meta: dict, bits, response) -> "Record":
        return Record(bits=np.asarray(bits, bool),
                      response=None if response is None else np.asarray(response, np.float32),
                      **{k: meta.get(k) if k in ("tag", "run_id") else meta[k] for k in Record.META_FIELDS})


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
