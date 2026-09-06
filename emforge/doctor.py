"""emforge/doctor.py — 機器體檢：版本、根目錄可寫與 O_EXCL、系統碟空間、HFSS 行程、舊 repo 綁定。

正式機部署腳本 `pull → doctor → worker` 綁在一起（I-10：只 pull 不重啟）。回非零就不准起 worker。
"""
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import _version, fs, model, netid
from .worker.workdir import default_work_root

MIN_FREE_GB = 5.0
WARN_FREE_GB = 20.0


def ansysedt_running() -> bool:
    """同一台機器只能有一個 HFSS 使用者（kill 殺全部 ansysedt.exe）；舊 worker 沒停就不准起新的。"""
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ansysedt.exe", "/NH"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "ansysedt.exe" in out.lower()


def probe_root(root) -> tuple:
    """try_claim 一個探針檔再刪——驗可寫與 O_EXCL。回 (ok, 說明)。"""
    probe = Path(root) / f".doctor_probe_{os.getpid()}"
    try:
        if not fs.try_claim(probe, {"at": model.now_iso()}):
            return False, "探針檔已存在（上次 doctor 沒清？）"
        fs.release(probe)
        return True, "可寫、O_EXCL 正常"
    except OSError as e:
        return False, f"不可寫：{e}"


def _free_gb(path: Path) -> float:
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free / 1e9


def run(root, *, hfss: bool = False, out=print) -> int:
    """印體檢表；回 0 可起 worker，4 有阻擋條件。"""
    rc = 0
    out(f"emforge     {_version.describe()}")
    out(f"python      {sys.version.split()[0]}  {sys.executable}")
    out(f"machine     tag={netid.local_tag()}  host={platform.node()}  ip={netid.local_ip()}")
    out(f"root        {root}")
    ok, msg = probe_root(root)
    out(f"root 探針   {msg}")
    if not ok:
        rc = 4
    work = default_work_root()
    free = _free_gb(work)
    flag = "" if free >= WARN_FREE_GB else ("  ⚠ 低於 20 GB" if free >= MIN_FREE_GB else "  ✗ 低於 5 GB，拒起（I-1）")
    out(f"work        {work}  系統碟剩餘 {free:.1f} GB{flag}")
    if free < MIN_FREE_GB:
        rc = 4
    out(f"antenna     EMFORGE_ANTENNA_REPO={os.environ.get('EMFORGE_ANTENNA_REPO') or '(未設)'}")
    if ansysedt_running():
        out("hfss        ✗ ansysedt.exe 已在跑——同機只能一個 HFSS 使用者（舊 worker 還沒停？）")
        rc = 4
    else:
        out("hfss        沒有 ansysedt.exe 在跑" + ("" if not hfss else "（--hfss 的 COM 連線探測留給 adapter）"))
    out("結論        " + ("OK，可起 worker" if rc == 0 else "有阻擋條件，先處理再起"))
    return rc
