"""emforge/_version.py — 版本戳。

`describe()` 是 `worker_ver` 的第一個成分（`emforge=<sha7>[+dirty]`）：worker 啟動時印、每筆結果蓋章、
report 對同一批出現兩種版本會警告。

#! 回歸 I-10（2026-07／08 兩犯）：改了 worker 端程式只 pull 不重啟＝跑舊程式且無人知道。
#  這裡不能防「沒重啟」，但版本蓋在每筆結果上，事後至少看得見是哪個版本量的。
"""
import subprocess
from pathlib import Path

from . import __version__

REPO_DIR = Path(__file__).resolve().parent.parent


def _git(repo: Path, *args: str) -> str | None:
    """跑一個 git 子命令；git 不在 PATH／不是 repo／逾時一律回 None（版本戳不准讓啟動失敗）。"""
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def git_sha(repo: Path = REPO_DIR) -> str | None:
    """`<sha7>` 或 `<sha7>+dirty`；拿不到回 None。未追蹤檔不算 dirty（工作目錄、暫存不該影響版本）。"""
    sha = _git(repo, "rev-parse", "--short", "HEAD")
    if sha is None:
        return None
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=no")
    return sha + ("+dirty" if dirty else "")


def describe() -> str:
    """`emforge=<sha7>[+dirty]`；非 git 安裝退回 `emforge=pkg-<版本>`。"""
    sha = git_sha()
    return f"emforge={sha}" if sha else f"emforge=pkg-{__version__}"
