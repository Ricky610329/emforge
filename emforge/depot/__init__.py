"""emforge/depot — 協調狀態的後端無關介面。

`Depot`（`base.py`）＝十幾個原語：文件（整份原子替換）／日誌（單寫者 append）／租約（互斥認領＋心跳＋過期破除）／
列舉（可最終一致）。`FileDepot` 逐位元重現今天的磁碟佈局；`MemoryDepot` 給測試與單行程 demo；
`open_depot(spec)` 由 `file://<root>`／`memory://<name>` 開後端。換後端＝實作抽象方法＋通過 `tests/depot/test_contract.py`。
"""
from ..fs import FsBusy, FsCorrupt, LockTimeout
from .base import Depot
from .file import FileDepot
from .memory import MemoryDepot
from .uri import open_depot

__all__ = ["Depot", "FileDepot", "MemoryDepot", "open_depot", "FsBusy", "FsCorrupt", "LockTimeout"]
