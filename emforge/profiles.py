"""emforge/profiles.py — 模擬庫：Profile 註冊表（append-only）、模擬器載入與開機前守門、退役、使用者註冊表。

守門順序固定在 `make_simulator`：載入類別 → 比 geom_ver → 比 labels → **才**建構（昂貴操作之前，I-6／I-7）。
使用者的 profile／spec 寫在 `<root>/registry.py`，runtime 與 worker 啟動都執行＝雙邊同源。

退役旗標（`db/<profile>/RETIRED`）是共享狀態 → 走 `Depot`；`registry.py` 是**程式碼**，用本機路徑 runpy 載入。
"""
import importlib
import runpy

import numpy as np

from . import paths
from .depot import open_depot
from .model import Profile, now_iso
from .specs import RegistryConflict  # noqa: F401 — 同一個衝突例外，兩個註冊表共用


class UnknownProfile(Exception):
    pass


class GeomVerMismatch(Exception):
    """註冊表的 geom_ver 與模擬器宣告不同（回歸 I-7：類別名沒變、幾何底板換代）。"""


class LabelsMismatch(Exception):
    """模擬器會產出的 labels 與 profile 宣告不同。"""


_PROFILES: dict = {}


def _same(a: Profile, b: Profile) -> bool:
    return (a.profile_hash == b.profile_hash and a.shape == b.shape and a.labels == b.labels
            and a.n_points == b.n_points and a.spec == b.spec and a.timeout_s == b.timeout_s
            and a.retired == b.retired and np.array_equal(a.fixed_on, b.fixed_on))


def register_profile(p: Profile) -> Profile:
    cur = _PROFILES.get(p.name)
    if cur is not None:
        if not _same(cur, p):
            raise RegistryConflict(f"profile {p.name!r} 已註冊且內容不同——改儀器請用新名字（era ≡ profile 名）")
        return cur
    _PROFILES[p.name] = p
    return p


def get_profile(name: str) -> Profile:
    try:
        return _PROFILES[name]
    except KeyError:
        raise UnknownProfile(f"profile {name!r} 未註冊（已註冊：{sorted(_PROFILES)}）") from None


def all_profiles() -> list:
    return [_PROFILES[k] for k in sorted(_PROFILES)]


def clear_registry() -> None:
    """測試用。"""
    _PROFILES.clear()


def load_simulator_class(profile: Profile) -> type:
    """`"module.path:ClassName"` → 類別。壞路徑直接拋（ValueError／ImportError／AttributeError），不吞。"""
    if ":" not in profile.simulator:
        raise ValueError(f"profile {profile.name}: simulator 要寫成 'module.path:ClassName'，得到 {profile.simulator!r}")
    mod_name, _, cls_name = profile.simulator.partition(":")
    return getattr(importlib.import_module(mod_name), cls_name)


def check_geom_ver(profile: Profile, sim_cls) -> None:
    """模擬器宣告 None＝單邊（只有 profile 說了算），不比對。"""
    declared = getattr(sim_cls, "geom_ver", None)
    if declared is not None and declared != profile.geom_ver:
        raise GeomVerMismatch(f"profile {profile.name} 說 geom_ver={profile.geom_ver!r}，"
                              f"模擬器 {sim_cls.__name__} 宣告 {declared!r}——幾何換代了？請用新 profile 名")


def check_labels(profile: Profile, sim_cls) -> None:
    declared = tuple(getattr(sim_cls, "labels", ()) or ())
    if declared and declared != profile.labels:
        raise LabelsMismatch(f"profile {profile.name} labels={profile.labels}，模擬器 {sim_cls.__name__} 產出 {declared}")


def make_simulator(profile: Profile, workdir):
    """守門在建構之前：載入 → geom_ver → labels → 建構。回傳實作 Simulator 協定的物件（尚未 open）。"""
    cls = load_simulator_class(profile)
    check_geom_ver(profile, cls)
    check_labels(profile, cls)
    return cls(workdir=str(workdir), profile=profile)


def is_retired(depot, profile: Profile) -> bool:
    """註冊表旗標或 `db/<profile>/RETIRED` 標記（CLI `retire` 寫的）任一為真。`depot` 吃 `Depot | str | Path`。"""
    return bool(profile.retired) or open_depot(depot).exists(paths.retired_marker(profile.name))


def retire(depot, profile: str, *, by: str) -> str:
    """寫退役標記；回它的 key。凍結＝拒收新工作，資料一個 byte 都不動。"""
    key = paths.retired_marker(profile)
    open_depot(depot).put_json(key, {"by": by, "at": now_iso()})
    return key


def load_user_registry(root) -> bool:
    """執行 `<root>/registry.py`（呼叫 register_profile／register_spec／register_measure）。沒有檔回 False。
    重跑＝重註冊同內容＝no-op；內容變了會撞 RegistryConflict（這正是要的）。"""
    p = paths.registry_py(root)
    if not p.exists():
        return False
    runpy.run_path(str(p), run_name="emforge_registry")
    return True
