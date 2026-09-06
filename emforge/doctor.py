"""emforge/doctor.py — 機器體檢：版本、本機 root 可寫、`Depot.selfcheck`（可寫／O_EXCL／時鐘偏移）、系統碟空間、HFSS 行程、舊 repo 綁定。

正式機部署腳本 `pull → doctor → worker` 綁在一起（I-10：只 pull 不重啟）。回非零就不准起 worker。
這個檔刻意留在本機層：C 槽、ansysedt 行程、root 探針都是這台機器的事，不是共享狀態。
"""
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import _version, fs, model, netid
from .depot import open_depot
from .worker.workdir import default_work_root

MIN_FREE_GB = 5.0
WARN_FREE_GB = 20.0


def ansysedt_running() -> bool:
    """同一台機器只能有一個 HFSS 使用者（kill 殺全部 ansysedt.exe）；舊 worker 沒停就不准起新的。"""
    if os.name != "nt":
        return False
    try:
        #! tasklist 用主控台字碼頁（cp950）輸出：以前 text=True 在 `-X utf8` 下解碼炸在讀取執行緒、stdout 變 None（M13 實抓）。
        #  只需要 ASCII 的 "ansysedt.exe"，用 errors="replace" 不管中文表頭。
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ansysedt.exe", "/NH"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "ansysedt.exe" in (out or "").lower()


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


def health(root, *, depot=None) -> dict:
    """機器事實（純函式、不印）：儀器層前置檢查與 `run` 共用。`blocking` 非空＝不准起 worker／開儀器。
    `depot` 給就跑 `selfcheck()`（時鐘偏移＝阻擋：租約全靠伺服器側 modified_at vs 本機 now）。"""
    root_ok, root_msg = probe_root(root)
    work = default_work_root()
    free = _free_gb(work)
    hfss = ansysedt_running()
    problems = [] if depot is None else list(open_depot(depot).selfcheck())
    blocking = []
    if not root_ok:
        blocking.append(f"root 不可寫：{root_msg}")
    blocking += [f"depot：{p}" for p in problems]
    if free < MIN_FREE_GB:
        blocking.append(f"系統碟剩餘 {free:.1f} GB 低於 {MIN_FREE_GB:.0f} GB（I-1）")
    if hfss:
        blocking.append("ansysedt.exe 已在跑——同機只能一個 HFSS 使用者（舊 worker 還沒停？）")
    return {"emforge_ver": _version.describe(), "python": sys.version.split()[0], "machine_tag": netid.local_tag(),
            "host": platform.node(), "ip": netid.local_ip(), "root": str(root), "root_ok": root_ok, "root_msg": root_msg,
            "depot_spec": None if depot is None else open_depot(depot).spec, "depot_problems": problems,
            "work_root": str(work), "free_gb": float(free), "ansysedt_running": hfss,
            "antenna_repo": os.environ.get("EMFORGE_ANTENNA_REPO") or None, "blocking": blocking}


def run(root, *, depot=None, hfss: bool = False, out=print) -> int:
    """印體檢表（`health()` 的人讀版）；回 0 可起 worker，4 有阻擋條件。"""
    h = health(root, depot=depot)
    out(f"emforge     {h['emforge_ver']}")
    out(f"python      {h['python']}  {sys.executable}")
    out(f"machine     tag={h['machine_tag']}  host={h['host']}  ip={h['ip']}")
    out(f"root        {h['root']}")
    out(f"root 探針   {h['root_msg']}")
    if h["depot_spec"] is not None:
        out(f"depot       {h['depot_spec']}  " + ("OK" if not h["depot_problems"] else "✗ " + "；".join(h["depot_problems"])))
    free = h["free_gb"]
    flag = "" if free >= WARN_FREE_GB else ("  ⚠ 低於 20 GB" if free >= MIN_FREE_GB else "  ✗ 低於 5 GB，拒起（I-1）")
    out(f"work        {h['work_root']}  系統碟剩餘 {free:.1f} GB{flag}")
    out(f"antenna     EMFORGE_ANTENNA_REPO={h['antenna_repo'] or '(未設)'}")
    if h["ansysedt_running"]:
        out("hfss        ✗ ansysedt.exe 已在跑——同機只能一個 HFSS 使用者（舊 worker 還沒停？）")
    else:
        out("hfss        沒有 ansysedt.exe 在跑" + ("" if not hfss else "（--hfss 的 COM 連線探測留給 adapter）"))
    out("結論        " + ("OK，可起 worker" if not h["blocking"] else "有阻擋條件，先處理再起：" + "；".join(h["blocking"])))
    return 0 if not h["blocking"] else 4
