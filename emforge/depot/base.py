"""emforge/depot/base.py — `Depot`：協調狀態的後端無關介面（`fs.py` O-3 的抽象版）。

四種語義，每個 key 只屬其一：
- **doc**：整份原子替換（`put_bytes`／`put_json`）；讀者只見完整舊版或完整新版。
- **log**：單寫者 append（`append`）；`rewrite_log` 整份替換給壓實用。
- **lease**：`claim` 互斥建立（payload 必含 `owner`）、`touch` 心跳、`break_if_stale` 過期破除、`release` 只刪自己的。
- **列舉**：`list(prefix)` 直接子項、排序、可最終一致；`newest` 衍生。

沒有 rename、沒有 compare-and-set：S3（`If-None-Match: *` PUT／版本化 DELETE）、SQL（唯一鍵 insert／`DELETE … WHERE updated_at<`）、
檔案系統（O_EXCL／os.replace）都做得到的最小集合。時鐘：`modified_at` 是伺服器側、`now()` 是本機——偏移由 `selfcheck` 量出，不修正。
"""
from __future__ import annotations  # 類別內的 `list` 方法會遮住內建 list，註解要延後求值

import contextlib
import json
import random
import time
from abc import ABC, abstractmethod

from .. import paths
from ..fs import FsCorrupt, LockTimeout
from ..model import now_iso


class Depot(ABC):
    spec: str = ""  # "file://<root>" / "memory://<name>"；子行程重開用（open_depot(spec)）

    # ── key 規則 ─────────────────────────────────────────────────────────────
    @staticmethod
    def check_key(key: str) -> str:
        """POSIX 相對路徑：非空、不以 "/" 開頭或結尾、無反斜線、無 ".." 段、**無冒號**（Windows 磁碟機代號／ADS）、
        段尾不是 "." 或空白（Windows 會靜默去掉＝別名）；根層不得是本機專屬名（registry.py／strategies／limits.json）。
        #! 回歸 I-26（2026-09-23）：`C:/…` 這種 key 以前放行，`Path(root) / "C:/x"` 直接換掉 root——遠端 /depot 可讀寫平台機任意檔；
        #  根層 registry.py／strategies/ 是平台機會**執行**的程式碼，不准經 Depot 寫。"""
        if not isinstance(key, str) or not key or key.startswith("/") or key.endswith("/") or "\\" in key or ":" in key:
            raise ValueError(f"壞 key：{key!r}")
        segs = key.split("/")
        if any(seg in ("", ".", "..") or seg[-1] in ". " for seg in segs):
            raise ValueError(f"壞 key：{key!r}")
        if segs[0] in paths.LOCAL_ONLY_ROOT_NAMES:
            raise ValueError(f"壞 key：{key!r}（{segs[0]} 是本機檔，不經 Depot）")
        return key

    @staticmethod
    def check_prefix(prefix: str) -> str:
        """前綴：空字串（根）或以 "/" 結尾。"""
        if not isinstance(prefix, str) or (prefix and not prefix.endswith("/")) or "\\" in prefix or prefix.startswith("/"):
            raise ValueError(f"壞 prefix（要以 / 結尾）：{prefix!r}")
        if prefix:
            Depot.check_key(prefix[:-1])
        return prefix

    @staticmethod
    def check_lease_payload(payload: dict) -> dict:
        if not isinstance(payload, dict) or not payload.get("owner"):
            raise ValueError("租約 payload 必含非空 owner")
        return payload

    # ── 文件 ─────────────────────────────────────────────────────────────────
    @abstractmethod
    def put_bytes(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def get_bytes(self, key: str) -> bytes | None: ...

    @abstractmethod
    def delete(self, key: str) -> bool:
        """True＝**這次呼叫**移除了它（競賽仲裁用）；缺 → False。"""

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    def put_json(self, key: str, obj) -> None:
        """canonical JSON（sort_keys／不轉義中文／indent=1）——與舊 `fs.atomic_write_json` 逐 byte 相同。序列化失敗不寫。"""
        self.put_bytes(key, json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8"))

    def get_json(self, key: str):
        """缺 → None；壞內容 → FsCorrupt。"""
        data = self.get_bytes(key)
        if data is None:
            return None
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise FsCorrupt(f"{self.spec}/{key}: {e}") from e

    def require_bytes(self, key: str) -> bytes:
        """「取不到就是錯」的讀法：缺 → FileNotFoundError（呼叫端把它當「這個 key 本來就該在」）。"""
        data = self.get_bytes(key)
        if data is None:
            raise FileNotFoundError(f"{self.spec}/{key}")
        return data

    def require_json(self, key: str):
        doc = self.get_json(key)
        if doc is None:
            raise FileNotFoundError(f"{self.spec}/{key}")
        return doc

    # ── 列舉／時間 ───────────────────────────────────────────────────────────
    @abstractmethod
    def list(self, prefix: str) -> list[str]:
        """prefix 以 "/" 結尾（"" ＝根）。回直接子項的完整 key、排序；子前綴帶尾 "/"；末段 "." 開頭者不列。可最終一致。"""

    @abstractmethod
    def modified_at(self, key: str) -> float | None:
        """伺服器側 epoch；缺 → None；同 key put/touch 後不減。"""

    @abstractmethod
    def set_modified_at(self, key: str, epoch: float) -> None:
        """測試／維運鉤子：把 modified_at 設成指定 epoch（模擬老租約）。"""

    def now(self) -> float:
        return time.time()

    def newest(self, prefix: str) -> float | None:
        """直接子項（不含子前綴）modified_at 的最大值；沒有 → None。"""
        ms = [self.modified_at(k) for k in self.list(prefix) if not k.endswith("/")]
        ms = [m for m in ms if m is not None]
        return max(ms) if ms else None

    def is_stale(self, key: str, stale_s: float, now: float | None = None) -> bool:
        """`now - modified_at > stale_s`；缺 → True（沒有心跳＝死）。"""
        m = self.modified_at(key)
        if m is None:
            return True
        return ((self.now() if now is None else now) - m) > stale_s

    # ── 日誌（單寫者） ───────────────────────────────────────────────────────
    @abstractmethod
    def append(self, key: str, record: dict) -> None: ...

    @abstractmethod
    def read_log(self, key: str) -> list[dict]:
        """缺 → []；壞行 → FsCorrupt。"""

    @abstractmethod
    def rewrite_log(self, key: str, records) -> None: ...

    # ── 租約 ─────────────────────────────────────────────────────────────────
    @abstractmethod
    def claim(self, key: str, payload: dict) -> bool:
        """互斥建立；已存在（含 delete-pending）→ False。payload 必含 "owner"。"""

    @abstractmethod
    def owner(self, key: str) -> dict | None:
        """租約 payload；缺／空／半截 → None。"""

    @abstractmethod
    def touch(self, key: str) -> bool:
        """刷新 modified_at；缺 → False 且**不建檔**（鎖被破後心跳變 no-op）。"""

    @abstractmethod
    def release(self, key: str, owner: str | None = None) -> bool:
        """給 owner 只刪自己的（不符／無法辨識 → False 不動）；None 強制；缺 → True。"""

    @abstractmethod
    def break_if_stale(self, key: str, stale_s: float, now: float | None = None) -> bool:
        """過期才移除；缺／新鮮 → False。並發破鎖者**可能不只一人 True**（Windows 兩個 rename 可都成功、S3 版本化 DELETE 冪等），
        但保證**不碰任何新建的租約**——破完必接 `claim`，claim 才是仲裁。"""

    @contextlib.contextmanager
    def lock(self, key: str, *, owner: str, stale_s: float = 180.0, timeout_s: float = 90.0,
             payload: dict | None = None, sleep=time.sleep):
        """`with depot.lock(key, owner=…):`——claim；拿不到就看持有者是否過期→破；每一圈都走逾時檢查＋sleep
        （review-5：claim 一直 False 而 key 又不在＝權限問題，不能忙迴圈）；逾時拋 LockTimeout（永不 SystemExit）。
        離開時 `release(owner=…)`：被破鎖又被別人重認領時不刪對方的。"""
        body = {"owner": owner, "at": now_iso(), **(payload or {})}
        t0 = self.now()
        while not self._claim_or_release(key, body, owner):
            if self.is_stale(key, stale_s):
                self.break_if_stale(key, stale_s)
            if self.now() - t0 > timeout_s:
                raise LockTimeout(f"{self.spec}/{key} 佔用 >{timeout_s:.0f}s——查殭屍鎖或後端權限")
            sleep(0.02 + random.random() * 0.03)
        try:
            yield self
        finally:
            self.release(key, owner=owner)

    def _claim_or_release(self, key: str, body: dict, owner: str) -> bool:
        """claim 的呼叫炸了（HTTP 回覆遺失、SMB 瞬斷）分不出「寫了沒」→ 先補一次 release(owner=自己)（冪等）再拋。
        #! 回歸 I-24（2026-09-23）：HttpDepot claim 已生效但回覆遺失時，lock() 直接拋、鎖殘留 180 s，jobs.lock 擋住整個機隊。"""
        try:
            return self.claim(key, body)
        except Exception:
            try:
                self.release(key, owner=owner)
            except Exception:  # noqa: BLE001
                pass
            raise

    # ── 其他 ─────────────────────────────────────────────────────────────────
    @abstractmethod
    def ensure_prefixes(self, prefixes) -> None:
        """init 用：讓 `list(prefix)` 之後合法（File＝mkdir；物件儲存＝no-op）。"""

    @abstractmethod
    def selfcheck(self) -> list[str]:
        """探針 claim/release、可寫、時鐘偏移；回問題清單（空＝健康）。"""
