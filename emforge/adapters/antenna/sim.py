"""emforge/adapters/antenna/sim.py — 把舊 HFSS 模擬器包成 emforge 的 Simulator 協定。

舊契約 `open() → 每筆 start(num) / __call__(Tensor) → {label: Tensor(17)} / end() → 秒 / quit()`；
這裡收成 `open() / simulate(bits) → SimResult / kill() / close()`；`num` 是內部計數器，worker 不需要知道。
建構**不碰 COM**（舊 PatchSimulator.__init__ 只建目錄），所以守門與建構都在 open 之前。
"""
import sys

import numpy as np

from ... import profiles as core_profiles
from ...model import SimResult, Simulator
from . import _bind
from .measure import DUAL_LABELS, N_POINTS, SINGLE_LABELS

#? record_path 由 worker 給（workdir）；HFSS_sab_path 是幾何底板＝geom_ver 的一部分，不准 profile 覆蓋。
FORBIDDEN_KWARGS = frozenset({"record_path", "HFSS_sab_path"})


class ProfileInconsistent(Exception):
    """profile 與這個模擬器包裝不一致（labels／shape／n_points／禁用 kwargs）。"""


class AdapterError(Exception):
    """模擬器回傳的東西不是協定要的形狀。"""


def _to_numpy(x):
    return x.detach().cpu().numpy() if hasattr(x, "detach") else np.asarray(x)


def _to_pattern(bits):
    arr = np.asarray(bits, np.float32)
    try:
        return _bind.load_torch().as_tensor(arr)
    except _bind.AntennaUnavailable:
        return arr                              # 沒 torch（純測試）就給 numpy，stub 不在意


class _AntennaSim(Simulator):
    LABELS = ()
    geom_ver = None
    CHECK_OLD_GEOM = False

    def __init__(self, *, workdir: str, profile, old_cls=None):
        """`old_cls` 只給測試注入 stub；正式路徑經 _bind 載入舊類別。"""
        super().__init__(workdir=workdir, profile=profile)
        self._validate(profile)
        cls = old_cls or self._load_old_cls()
        if self.CHECK_OLD_GEOM:
            self._check_old_geom(cls)
        self._sim = cls(record_path=str(workdir), **dict(profile.kwargs))
        self._n = 0

    def _validate(self, profile) -> None:
        bad = sorted(set(profile.kwargs) & FORBIDDEN_KWARGS)
        if bad:
            raise ProfileInconsistent(f"profile {profile.name} kwargs 不可含 {bad}（工作目錄由 worker 給；底板＝geom_ver 的一部分）")
        if tuple(profile.labels) != self.LABELS:
            raise ProfileInconsistent(f"profile {profile.name} labels={profile.labels}，{type(self).__name__} 產出 {self.LABELS}")
        pc = int(profile.kwargs.get("pixel_count", 25))
        if tuple(profile.shape) != (pc, pc):
            raise ProfileInconsistent(f"profile {profile.name} shape={profile.shape} ≠ (pixel_count, pixel_count)=({pc}, {pc})")
        if profile.n_points != N_POINTS:
            raise ProfileInconsistent(f"profile {profile.name} n_points={profile.n_points}，HFSS 掃頻固定 {N_POINTS} 點")

    def _load_old_cls(self):
        raise NotImplementedError

    def _check_old_geom(self, cls) -> None:
        """雙邊比對（I-7）：舊模擬器類別或其模組要宣告 GEOM_VER，且等於 adapter 宣告。"""
        declared = getattr(cls, "GEOM_VER", None)
        if declared is None:
            declared = getattr(sys.modules.get(cls.__module__), "GEOM_VER", None)
        if declared is None:
            raise core_profiles.GeomVerMismatch(f"舊模擬器 {cls.__name__} 沒有 GEOM_VER 常數——雙邊比對做不到")
        if declared != self.geom_ver:
            raise core_profiles.GeomVerMismatch(f"舊模擬器宣告 GEOM_VER={declared!r}，adapter 宣告 {self.geom_ver!r}——幾何換代了？請開新 profile／新 adapter 版")

    # ── 協定 ──
    def open(self) -> None:
        self._sim.open()

    def simulate(self, bits) -> SimResult:
        pattern = _to_pattern(bits)
        self._n += 1
        self._sim.start(self._n)
        try:
            out = self._sim(pattern)
        except Exception:
            try:
                self._sim.end(save_project=False)   # 炸了也要把 HFSS 專案關掉，不存
            except Exception:  # noqa: BLE001
                pass
            raise
        elapsed = self._sim.end()
        return SimResult(response=self._stack(out), time_s=float(elapsed or 0.0), extra=self._extra())

    def _stack(self, out) -> np.ndarray:
        missing = [l for l in self.LABELS if l not in out]
        if missing:
            raise AdapterError(f"模擬器回傳缺 labels {missing}（有 {sorted(out)}）")
        rows = [np.asarray(_to_numpy(out[l]), np.float32).reshape(-1) for l in self.LABELS]
        bad = [(l, int(r.shape[0])) for l, r in zip(self.LABELS, rows) if r.shape[0] != N_POINTS]
        if bad:
            raise AdapterError(f"響應點數不對 {bad}（每個 label 要 {N_POINTS} 點）")
        return np.stack(rows)

    def _extra(self) -> dict:
        return {}

    def kill(self) -> None:
        try:
            self._sim.kill()
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        try:
            self._sim.quit()
        except Exception:  # noqa: BLE001
            pass


class DualPortSim(_AntennaSim):
    """二埠濾波天線；舊 `dual_port.DualPortSimulator`（GEOM_VER 雙邊比對）。"""
    LABELS = DUAL_LABELS
    geom_ver = "p01"
    CHECK_OLD_GEOM = True

    def _load_old_cls(self):
        return _bind.load_sims().dual_port.DualPortSimulator


class SinglePortRadSim(_AntennaSim):
    """單埠天線含方向圖；舊 `single_port_rad.SinglePortRadSimulator`。
    舊模組沒有 GEOM_VER（Ricky 2026-09-01：不動舊 repo）→ 單邊宣告 s00，不比對。方向圖走 extra["radiation"]。"""
    LABELS = SINGLE_LABELS
    geom_ver = "s00"
    CHECK_OLD_GEOM = False

    def _load_old_cls(self):
        return _bind.load_sims().single_port_rad.SinglePortRadSimulator

    def _extra(self) -> dict:
        rad = getattr(self._sim, "last_radiation", None)
        if not isinstance(rad, dict) or "theta" not in rad:
            return {}
        return {"radiation": {k: [float(v) for v in np.asarray(rad[k]).reshape(-1)]
                              for k in ("theta", "phi0", "phi90") if k in rad}}
