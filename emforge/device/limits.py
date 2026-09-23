"""emforge/device/limits.py — MHS 第 2–4 層：硬限制（`Limits`）、前置檢查（重用 `worker/gate.check`＋`doctor.health`）。
兩段式確認的 token 由 `Instrument.issue_confirm／check_confirm／consume_confirm` 管（每次不同、綁 op/key、`confirm_window_s` 到期，
檢查 #9）；這裡只留窗長的預設值。

限制是機制不含政策：值由 profile／doctor 現有常數與部署設定 `<root>/limits.json`（本機檔，`load_limits`；M16）來。
"""
import json
from dataclasses import asdict, dataclass

from .. import doctor, paths, profiles
from ..depot import open_depot
from ..worker.gate import check as gate_check

DEFAULT_MAX_SAMPLE_S = 3600.0
DEFAULT_CONFIRM_WINDOW_S = 600.0


@dataclass(frozen=True)
class Limits:
    allowed_profiles: tuple = ()             # 空＝本機註冊表全部
    max_sample_s: float = DEFAULT_MAX_SAMPLE_S   # profile.timeout_s 不得超過（單筆看門狗上限）
    min_free_gb: float = doctor.MIN_FREE_GB
    confirm_window_s: float = DEFAULT_CONFIRM_WINDOW_S

    def __post_init__(self):
        #! 檢查 #16（2026-09-07）：以前只擋壞 JSON、不驗型別——"allowed_profiles": "dual"（少一對中括號）逐字元變 tuple＝全部 profile 不准；
        #  "max_sample_s": "1800" 開機時 TypeError 被當機器卡住。現在指名欄位拒。
        ap = self.allowed_profiles
        if isinstance(ap, str) or not isinstance(ap, (list, tuple)) or not all(isinstance(x, str) and x for x in ap):
            raise ValueError(f"allowed_profiles 要是 profile 名字的 list，拿到 {ap!r}")
        object.__setattr__(self, "allowed_profiles", tuple(ap))
        for name in ("max_sample_s", "min_free_gb", "confirm_window_s"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not v > 0:
                raise ValueError(f"{name} 要是正數，拿到 {v!r}")
            object.__setattr__(self, name, float(v))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["allowed_profiles"] = list(self.allowed_profiles)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Limits":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


#? `emforge init` 寫的範本＝預設值全列（欄位與 Limits 一致，test_limits 釘）；每台改自己的。
LIMITS_TEMPLATE = Limits().to_dict()


def load_limits(root) -> tuple:
    """`<root>/limits.json` → (Limits, 來源)；沒有＝(預設, "default")；壞 JSON 指名檔案拋 ValueError（不默默放掉上限）。"""
    p = paths.limits_json(root)
    if not p.exists():
        return Limits(), "default"
    try:
        d = json.loads(p.read_text(encoding="utf-8-sig"))     # PowerShell 5 的 Set-Content -Encoding utf8 會寫 BOM（I-33）
    except json.JSONDecodeError as e:
        raise ValueError(f"{p}：limits.json 不是合法 JSON（{e}）") from None
    if not isinstance(d, dict):
        raise ValueError(f"{p}：limits.json 頂層要是物件")
    try:
        return Limits.from_dict(d), str(p)
    except ValueError as e:
        raise ValueError(f"{p}：limits.json 欄位不對——{e}") from None


def check_preconditions(profile, *, limits: Limits, depot, root, health: dict | None = None) -> list:
    """開儀器前的全部檢查；回問題清單（空＝可開）。順序：允許名單 → 守門（註冊／退役／hash／geom／labels）→ 逾時上限 → 機器體檢。"""
    problems = []
    if limits.allowed_profiles and profile.name not in limits.allowed_profiles:
        problems.append(f"profile {profile.name} 不在 allowed_profiles {list(limits.allowed_profiles)}")
    v = gate_check(profile.name, profile.profile_hash, open_depot(depot))
    if not v.ok:
        problems.append(v.reason)
    elif profiles.is_retired(depot, profile):
        problems.append(f"profile_retired: {profile.name}")
    if float(profile.timeout_s) > limits.max_sample_s:
        problems.append(f"profile timeout_s {profile.timeout_s} 超過 max_sample_s {limits.max_sample_s:.0f}")
    h = health if health is not None else doctor.health(root, depot=depot)
    problems += list(h.get("blocking", []))
    if h.get("free_gb") is not None and float(h["free_gb"]) < limits.min_free_gb and not any("GB" in p for p in problems):
        problems.append(f"系統碟剩餘 {h['free_gb']:.1f} GB 低於 min_free_gb {limits.min_free_gb:.0f}")
    return problems
