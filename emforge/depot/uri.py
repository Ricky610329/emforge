"""emforge/depot/uri.py — `open_depot(spec)`：由 spec 開後端。

`file://<root>`（`file://C:/x`、`file:///C:/x`、`file:///home/x`、`file:////server/share`）／`memory://<name>`（行程級單例；**未註冊的名字拋**）／
無 `://` 視為本機路徑／`Depot` 實例透傳。環境變數 `EMFORGE_DEPOT`、CLI `--depot` 都吃這個格式；未來 `s3://`、`sql://` 在這裡註冊。
"""
import os
from pathlib import Path

from .base import Depot
from .file import FileDepot
from .memory import registered


def open_depot(spec) -> Depot:
    if isinstance(spec, Depot):
        return spec
    if isinstance(spec, os.PathLike):
        return FileDepot(spec)
    if not isinstance(spec, str):
        raise TypeError(f"depot spec 要是 str／Path／Depot：{type(spec).__name__}")
    if "://" not in spec:
        return FileDepot(spec)
    scheme, _, rest = spec.partition("://")
    if scheme in ("http", "https"):
        from .http import HttpDepot
        return HttpDepot(spec)
    if scheme == "file":
        return FileDepot(_file_root(rest))
    if scheme == "memory":
        if not rest:
            raise ValueError("memory:// 要帶名字：memory://<name>")
        d = registered(rest)
        if d is None:
            #! 檢查 #31／#27（2026-09-07）：以前未註冊就新建一個空的——子行程 `--depot memory://x` 會「成功」開出空庫，策略靜默看到零資料。
            raise ValueError(f"memory://{rest} 不存在於本行程：memory depot 要先 MemoryDepot(name=…) 建好再用（子行程看不到）")
        return d
    raise ValueError(f"未知的 depot scheme：{scheme!r}（支援 file://、memory://）")


def _file_root(rest: str) -> Path:
    if len(rest) >= 3 and rest[0] == "/" and rest[2] == ":":  # RFC 8089 的 /C:/x → C:/x
        rest = rest[1:]
    if not rest:
        raise ValueError("file:// 要帶路徑：file://<root>")
    return Path(rest)
