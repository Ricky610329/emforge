"""emforge/device/limits.py — MHS 第 2–4 層：硬限制（`Limits`）、前置檢查（重用 `worker/gate.check`＋`doctor.health`）、
兩段式確認（`confirm_token`／`confirm_ok`：sha1(secret|op|key|10 分鐘窗)[:8]，單次使用）。

限制是機制不含政策：值由 profile／doctor 現有常數與部署設定 `<root>/limits.json`（本機檔，`load_limits`；M16）來。
"""
import hashlib
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
        object.__setattr__(self, "allowed_profiles", tuple(self.allowed_profiles))

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
        d = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{p}：limits.json 不是合法 JSON（{e}）") from None
    if not isinstance(d, dict):
        raise ValueError(f"{p}：limits.json 頂層要是物件")
    return Limits.from_dict(d), str(p)


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


def _window(now: float, window_s: float) -> int:
    return int(now // window_s)


def _token(secret: str, op: str, key: str, window: int) -> str:
    return hashlib.sha1(f"{secret}|{op}|{key}|{window}".encode("utf-8")).hexdigest()[:8]


def confirm_token(secret: str, op: str, key: str, now: float, window_s: float = DEFAULT_CONFIRM_WINDOW_S) -> str:
    """兩段式確認的 token：綁操作與對象（op／key），10 分鐘窗；同窗內穩定。"""
    return _token(secret, op, key, _window(now, window_s))


def confirm_valid(secret: str, op: str, key: str, token: str, *, used: set, now: float,
                  window_s: float = DEFAULT_CONFIRM_WINDOW_S) -> bool:
    """只驗不消費：本窗或上一窗（窗邊界寬限）的 token、且沒用過。操作真的開始才 `used.add`——被拒不燒 token。"""
    if not token or token in used:
        return False
    w = _window(now, window_s)
    return token in (_token(secret, op, key, w), _token(secret, op, key, w - 1))


def confirm_ok(secret: str, op: str, key: str, token: str, *, used: set, now: float,
               window_s: float = DEFAULT_CONFIRM_WINDOW_S) -> bool:
    """驗證＋消費（記進 `used`，單次使用）。"""
    if not confirm_valid(secret, op, key, token, used=used, now=now, window_s=window_s):
        return False
    used.add(token)
    return True
