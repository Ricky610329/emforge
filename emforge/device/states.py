"""emforge/device/states.py — 儀器狀態字典（MHS 的 states）：`devices/<tag>/state.json`。

欄位唯一真相＝`DeviceState`；**只有該台 `Instrument` 寫**（轉換即寫＋心跳），讀者（runtime、`fleet`、MCP）用 `modified_at`
推導 `offline`——儀器自己不寫「我離線」（死掉的儀器寫不了）。寫失敗吞掉、記裝置日誌：NAS 斷線不能殺儀器。
"""
from dataclasses import asdict, dataclass, field, fields

from .. import paths
from ..depot import open_depot
from ..model import now_iso

STATES = ("idle", "opening", "ready", "busy", "estop", "fault")
DEFAULT_OFFLINE_S = 90.0     #? 心跳 30 s；三拍沒到就當離線


@dataclass
class DeviceState:
    tag: str
    at: str = ""
    state: str = "idle"
    owner: str | None = None            # 租約持有者：queue:<store>／mcp:<by>
    store: str | None = None
    profile: str | None = None
    profile_hash: str | None = None
    current_id: str | None = None
    sample_started_at: str | None = None
    n_done: int = 0
    n_error: int = 0
    last_result_at: str | None = None
    last_error: str | None = None
    worker_ver: str = ""
    pid: int = 0
    url: str | None = None              # MCP endpoint（M15）
    free_gb: float | None = None
    ansysedt_running: bool | None = None
    estop: dict | None = None           # 急停中的 {scope, by, reason, at}
    limits: dict = field(default_factory=dict)
    started_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DeviceState":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


def write_state(depot, st: DeviceState, *, log=None) -> bool:
    """整份原子替換；失敗回 False（記 device_fault，log 本身失敗也吞）。"""
    st.at = now_iso()
    try:
        open_depot(depot).put_json(paths.device_state(st.tag), st.to_dict())
        return True
    except Exception as e:  # noqa: BLE001 — NAS 斷線不能殺儀器
        if log is not None:
            try:
                log("device_fault", error=f"write_state: {type(e).__name__}: {e}")
            except Exception:  # noqa: BLE001
                pass
        return False


def read_state(depot, tag: str) -> DeviceState | None:
    d = open_depot(depot).get_json(paths.device_state(tag))
    return DeviceState.from_dict(d) if d else None


def read_fleet(depot, *, offline_s: float = DEFAULT_OFFLINE_S, now: float | None = None) -> list:
    """機隊：每台的狀態字典＋讀者推導的 `offline`／`age_s`；依 tag 排序。列到但讀不到的跳過。"""
    d = open_depot(depot)
    now = d.now() if now is None else now
    out = []
    for tag in paths.dir_names(d.list(paths.devices_dir())):
        key = paths.device_state(tag)
        doc, m = d.get_json(key), d.modified_at(key)
        if not doc or m is None:
            continue
        age = max(0.0, now - m)
        out.append({**doc, "age_s": round(age, 1), "offline": age > offline_s})
    return out
