"""emforge/worker/workdir.py — 本機工作目錄生命週期：`<EMFORGE_WORK>/<store>/`。

#! 回歸 I-1（2026-07-15）：78 個工作暫存吃光系統碟 → 求解器連環例外，重開機假好轉。兩道清理：
#  run_batch 結束（done／fail／yield 皆）刪；worker 啟動時整清（那時本機不可能有活的 run）。
預設根在 %LOCALAPPDATA%\\emforge\\work（不在 repo、不在 NAS）。
"""
import os
import shutil
import tempfile
from pathlib import Path

from .. import fs


def default_work_root() -> Path:
    env = os.environ.get("EMFORGE_WORK")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "emforge" / "work"


class WorkDir:
    def __init__(self, root):
        self.root = Path(root)

    def path(self, store: str) -> Path:
        return self.root / store

    def make(self, store: str) -> Path:
        p = self.path(store)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def remove(self, store: str) -> None:
        shutil.rmtree(self.path(store), ignore_errors=True)

    def sweep_all(self) -> list:
        """刪根下所有子目錄（啟動清掃）；回刪掉的路徑。"""
        return fs.sweep_dirs(self.root)
