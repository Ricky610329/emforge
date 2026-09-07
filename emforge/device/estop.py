"""emforge/device/estop.py — 急停（MHS 第 5 層）。三層、任一層有效就停：

  fleet   `queue/ESTOP`         全機隊（CLI engage/clear）
  device  `queue/ESTOP.<tag>`   單機（CLI engage/clear）
  local   `<root>/ESTOP`        本機檔（root 在本機碟時 NAS 斷線也擋得住——deploy.md §2 兩個根分開；本機 watchdog／人手建）

儀器 `open()`／`simulate()` 前硬檢查（拋 `EstopEngaged`）；worker 每筆前讓位、job 之間不撿；正在跑的那筆 `abort()`。
**解除只能 CLI**（`emforge device-estop clear`）——MCP 沒有這條路，設計如此。
"""
from pathlib import Path

from .. import paths
from ..depot import open_depot
from ..model import now_iso

SCOPE_FLEET, SCOPE_DEVICE, SCOPE_LOCAL = "fleet", "device", "local"


class EstopEngaged(Exception):
    """急停中：`scope` 說是哪一層。"""

    def __init__(self, info: dict):
        self.info, self.scope = info, info.get("scope")
        super().__init__(f"急停中（{self.scope}，{info.get('by')}：{info.get('reason')}，{info.get('at')}）")


def _doc(by: str, reason: str) -> dict:
    return {"by": by, "reason": str(reason), "at": now_iso()}


def engaged(depot, root, tag: str) -> dict | None:
    """回最外層有效的急停 {scope, by, reason, at}；沒有回 None。順序 fleet → device → local。"""
    d = open_depot(depot)
    for scope, key in ((SCOPE_FLEET, paths.estop_fleet()), (SCOPE_DEVICE, paths.estop_device(tag))):
        if d.exists(key):
            return {"scope": scope, **(d.get_json(key) or {})}
    p = paths.estop_local(root)
    if p.exists():
        return {"scope": SCOPE_LOCAL, "by": "local", "reason": p.read_text(encoding="utf-8").strip() or "本機 ESTOP 檔",
                "at": now_iso()}
    return None


def engage(depot, tag: str | None = None, *, by: str, reason: str) -> str:
    """建急停檔（tag None＝全機）；回 key。重複 engage 覆寫（最新的 by／reason）。"""
    key = paths.estop_fleet() if tag is None else paths.estop_device(tag)
    open_depot(depot).put_json(key, _doc(by, reason))
    return key


def clear(depot, tag: str | None = None) -> bool:
    """解除（只給 CLI）；回 True＝這次清掉了、False＝本來就沒有。"""
    return open_depot(depot).delete(paths.estop_fleet() if tag is None else paths.estop_device(tag))


def engage_local(root, *, by: str, reason: str) -> Path:
    p = paths.estop_local(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"{by}: {reason} ({now_iso()})\n", encoding="utf-8")
    return p


def clear_local(root) -> bool:
    p = paths.estop_local(root)
    if not p.exists():
        return False
    p.unlink()
    return True


def check(depot, root, tag: str) -> None:
    """硬檢查：急停中就拋 `EstopEngaged`。"""
    info = engaged(depot, root, tag)
    if info is not None:
        raise EstopEngaged(info)
