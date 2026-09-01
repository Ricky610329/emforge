"""emforge/worker/gate.py — 開模擬器前守門。順序固定、擋在任何昂貴操作之前、不建構不 open。

  1. profile 在本機註冊表且未退役
  2. job.profile_hash == 註冊表的 profile_hash（派工端與執行端看到同一個儀器；不符＝版本或註冊表不同步）
  3. 載入模擬器類別，geom_ver 雙邊比對（模擬器宣告 None＝單邊，跳過）
  4. labels 比對

#! 回歸 I-6（2026-08-31）：漏帶設定 → 單埠模擬器量雙埠設計，結果欄位齊全、數字合理、**不報錯**。
#! 回歸 I-7（2026-08-13）：類別名沒變、幾何底板換代。
"""
from dataclasses import dataclass

from .. import profiles
from ..model import Job, Profile


@dataclass(frozen=True)
class GateVerdict:
    ok: bool
    reason: str | None = None
    profile: Profile | None = None
    sim_cls: type | None = None


def gate(job: Job, root) -> GateVerdict:
    try:
        profile = profiles.get_profile(job.sim_profile)
    except profiles.UnknownProfile as e:
        return GateVerdict(False, f"unknown_profile: {e}")
    if profiles.is_retired(root, profile):
        return GateVerdict(False, f"profile_retired: {profile.name}")
    if job.profile_hash != profile.profile_hash:
        return GateVerdict(False, f"profile_hash_mismatch: job={job.profile_hash} registry={profile.profile_hash}"
                                  f"（派工端與執行端的 emforge 版本或 registry.py 不同步）")
    try:
        cls = profiles.load_simulator_class(profile)
    except Exception as e:  # noqa: BLE001 — 壞路徑、缺套件都算守門不過
        return GateVerdict(False, f"simulator_load_failed: {type(e).__name__}: {e}")
    try:
        profiles.check_geom_ver(profile, cls)
    except profiles.GeomVerMismatch as e:
        return GateVerdict(False, f"geom_ver_mismatch: {e}")
    try:
        profiles.check_labels(profile, cls)
    except profiles.LabelsMismatch as e:
        return GateVerdict(False, f"labels_mismatch: {e}")
    return GateVerdict(True, None, profile, cls)
