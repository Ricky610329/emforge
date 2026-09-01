"""emforge/adapters/antenna/_bind.py — 綁舊 Antenna repo：`EMFORGE_ANTENNA_REPO` → sys.path → lazy import。

為什麼用 env var 而不是 PYTHONPATH：只有轉接層需要它、範圍明確；doctor／worker 啟動可以印出「綁到哪個 clone、哪個 sha」
（worker_ver 的第二個成分）；PYTHONPATH 會把整個舊 repo 暴露給核心，讓「核心零 antenna import」只剩守門測試在擋。
`antenna.patch` 的 import 鏈含 `from script.kill import kill`，所以要放 **repo 根**進 sys.path，不是 `antenna/`。
"""
import importlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ENV = "EMFORGE_ANTENNA_REPO"


class AntennaUnavailable(Exception):
    """舊 repo 不可 import：訊息帶怎麼修。"""


def repo_root() -> Path | None:
    raw = os.environ.get(ENV)
    if not raw:
        return None
    p = Path(raw)
    return p if (p / "antenna" / "__init__.py").exists() else None


def _ensure_path() -> None:
    r = repo_root()
    if r is not None and str(r) not in sys.path:
        sys.path.insert(0, str(r))
    os.environ.setdefault("MPLBACKEND", "Agg")      # antenna.losses 會拉 matplotlib.pyplot；無顯示環境要 Agg


def _import(name: str):
    _ensure_path()
    try:
        return importlib.import_module(name)
    except ImportError as e:
        raise AntennaUnavailable(f"無法 import {name}（{e}）。請設 {ENV}=<Antenna repo 根目錄>，"
                                 f"並確認此環境有 torch／pywin32／psutil。") from e


def load_losses():
    """`antenna.losses`（只拉 torch/numpy/matplotlib，不碰 win32com）。"""
    return _import("antenna.losses")


def load_sims() -> SimpleNamespace:
    """舊模擬器模組（會拉 win32com、psutil）。"""
    return SimpleNamespace(dual_port=_import("antenna.patch.patch_simulator.dual_port"),
                           single_port_rad=_import("antenna.patch.patch_simulator.single_port_rad"))


def load_torch():
    return _import("torch")


def antenna_sha() -> str:
    """舊 repo 的 git 短 sha（＋dirty）；拿不到回 "unknown"。"""
    r = repo_root()
    if r is None:
        return "unknown"
    try:
        sha = subprocess.run(["git", "-C", str(r), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10)
        if sha.returncode != 0:
            return "unknown"
        dirty = subprocess.run(["git", "-C", str(r), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return sha.stdout.strip() + ("+dirty" if dirty else "")
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
