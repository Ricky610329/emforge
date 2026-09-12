"""emforge/worker/workdir.py — 本機工作目錄生命週期：`<EMFORGE_WORK>/<store>/`。

#! 回歸 I-1（2026-07-15）：78 個工作暫存吃光系統碟 → 求解器連環例外，重開機假好轉。兩道清理：
#  run_batch 結束（done／fail／yield 皆）刪；worker 啟動時整清（那時本機不可能有活的 run）。
預設根在 %LOCALAPPDATA%\\emforge\\work（不在 repo、不在 NAS）。
"""
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from .. import fs, paths
from ..depot import FileDepot
from ..runner.process import process_identity


def default_work_root() -> Path:
    env = os.environ.get("EMFORGE_WORK")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "emforge" / "work"


class WorkDir:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.local = FileDepot(paths.worker_state_root(self.root))
        self._owner = None

    def acquire(self):
        """清理與發布裝置狀態之前，取得此工作目錄的行程所有權。"""
        if self._owner is not None:
            return
        owner = uuid.uuid4().hex
        with self.local.lock(paths.worker_lock_guard(), owner=owner):
            key = paths.worker_lock()
            old = self.local.owner(key)
            if self.local.exists(key):
                if not old or not {"owner", "pid", "birth"} <= old.keys():
                    raise RuntimeError("工作目錄鎖無法辨識，拒絕清理")
                if process_identity(old["pid"]) == old["birth"]:
                    raise RuntimeError("工作目錄已有活躍 worker／儀器行程")
                self.local.release(key, owner=old["owner"])
            payload = {"owner": owner, "pid": os.getpid(), "birth": process_identity(os.getpid())}
            if not self.local.claim(key, payload):
                raise RuntimeError("工作目錄所有權已被占用")
            self._owner = owner

    def release(self):
        if self._owner is not None:
            self.local.release(paths.worker_lock(), owner=self._owner)
            self._owner = None

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
