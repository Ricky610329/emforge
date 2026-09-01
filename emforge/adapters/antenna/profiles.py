"""emforge/adapters/antenna/profiles.py — 這個域的 measures／specs／profiles；`register_all()` 交給 `<root>/registry.py` 呼叫。

profile 名＝era：`dual_p01_db075`（現役：p01 底板、菱形橋 0.075 mm）、`single_db100`（交付主批用的 0.100 mm）。
退役的（只為匯入歷史資料而存在，拒收新工作）：dual_p00／dual_p01／single_p00／harvest_dual／harvest_single。
kwargs 全部**顯式**寫出（不吃舊建構子預設），並由 tests/adapters/test_antenna_ctor.py 對帳舊建構子簽名。
"""
import numpy as np

from ... import profiles as core
from ... import specs
from ...model import Profile, Spec
from .measure import (DUAL_LABELS, DUAL_R1_TARGETS, MEASURE_DUAL_R1, MEASURE_SINGLE_BASE, N_POINTS, SINGLE_BASE_TARGETS,
                      SINGLE_LABELS, measure_dual_r1, measure_single_base)

DUAL_SIM = "emforge.adapters.antenna.sim:DualPortSim"
SINGLE_SIM = "emforge.adapters.antenna.sim:SinglePortRadSim"
SHAPE = (25, 25)
#? 饋墊 (r0, r1, c0, c1)：同源舊 repo `PORT_SPECS[...]["feeds"]`／`dedust.DUAL_FEEDS`；single 只有底部（FEED=(24,12) 在其中）。
DUAL_FEEDS = ((0, 5, 10, 15), (20, 25, 10, 15))
SINGLE_FEEDS = ((20, 25, 10, 15),)
HFSS_DEFAULTS = {"max_delta_s": 0.02, "max_passes": 6, "min_passes": 5, "min_converged": 5}
TIMEOUT_S = 900


def feed_mask(shape, feeds) -> np.ndarray:
    m = np.zeros(shape, bool)
    for r0, r1, c0, c1 in feeds:
        m[r0:r1, c0:c1] = True
    return m


def dual_profile(name: str, *, geom_ver: str = "p01", diag_bridge_w=None, retired: bool = False, kwargs=None) -> Profile:
    kw = {"pixel_count": 25, "sweep_type": "Fast", **HFSS_DEFAULTS} if kwargs is None else dict(kwargs)
    if diag_bridge_w is not None:
        kw["diag_bridge_w"] = diag_bridge_w
    return Profile(name=name, simulator=DUAL_SIM, geom_ver=geom_ver, kwargs=kw, shape=SHAPE, labels=DUAL_LABELS,
                   n_points=N_POINTS, fixed_on=feed_mask(SHAPE, DUAL_FEEDS), measure=MEASURE_DUAL_R1, spec="dual_v2",
                   timeout_s=TIMEOUT_S, retired=retired)


def single_profile(name: str, *, diag_bridge_w=None, retired: bool = False, kwargs=None) -> Profile:
    kw = {"pixel_count": 25, "sweep_type": "Interpolating", **HFSS_DEFAULTS} if kwargs is None else dict(kwargs)
    if diag_bridge_w is not None:
        kw["diag_bridge_w"] = diag_bridge_w
    return Profile(name=name, simulator=SINGLE_SIM, geom_ver="s00", kwargs=kw, shape=SHAPE, labels=SINGLE_LABELS,
                   n_points=N_POINTS, fixed_on=feed_mask(SHAPE, SINGLE_FEEDS), measure=MEASURE_SINGLE_BASE,
                   spec="single_v1", timeout_s=TIMEOUT_S, retired=retired)


def all_profiles() -> list:
    return [
        dual_profile("dual_p01_db075", diag_bridge_w=0.075),
        single_profile("single_db100", diag_bridge_w=0.100),
        # ── 退役：只為匯入歷史資料（Seam 4），gate 拒收新工作 ──
        dual_profile("dual_p00", geom_ver="p00", retired=True),
        dual_profile("dual_p01", retired=True),
        single_profile("single_p00", retired=True),
        dual_profile("harvest_dual", geom_ver="p00", retired=True, kwargs={"pixel_count": 25}),
        single_profile("harvest_single", retired=True, kwargs={"pixel_count": 25}),
    ]


def register_all() -> None:
    """可重跑（同內容＝no-op）。"""
    specs.register_measure(MEASURE_DUAL_R1, measure_dual_r1, labels=DUAL_LABELS, targets=DUAL_R1_TARGETS)
    specs.register_measure(MEASURE_SINGLE_BASE, measure_single_base, labels=SINGLE_LABELS, targets=SINGLE_BASE_TARGETS)
    specs.register_spec(Spec(name="dual_v1", labels=DUAL_LABELS, measure=MEASURE_DUAL_R1,
                             axes=("m1", "m2", "m3", "m4"), offsets=(0.0, 0.0, 0.0, 0.0)))
    #? 規格 v2（2026-08-12 學長裁定：帶內 −12→−10、阻帶 −20→−15、帶外退場）＝評分層平移，量測不動
    specs.register_spec(Spec(name="dual_v2", labels=DUAL_LABELS, measure=MEASURE_DUAL_R1,
                             axes=("m1", "m2", "m3", "m4"), offsets=(2.0, 2.0, 0.0, 5.0)))
    specs.register_spec(Spec(name="single_v1", labels=SINGLE_LABELS, measure=MEASURE_SINGLE_BASE,
                             axes=("S11", "Gain"), offsets=(0.0, 0.0)))
    for p in all_profiles():
        core.register_profile(p)
