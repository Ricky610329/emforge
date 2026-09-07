# -*- coding: utf-8 -*-
"""tests/test_smoke.py — 套件層級的靜態守門：這些不是功能測試，是**規範**的機器版。

防什麼：
- 核心偷偷 import torch/antenna（平台零領域依賴的硬需求）。
- 檔案長成第二個 dedust.py（Ricky 2026-09-01：「模組化、可維護」）。
- 命名規範文件與程式碼脫鉤。
"""
import ast
from pathlib import Path

import emforge
from emforge import paths

PKG = Path(emforge.__file__).resolve().parent
DOCS = PKG.parent / "docs"

#? 核心禁用的頂層套件：領域／重型數值庫只能出現在 adapters/ 與 legacy/。
FORBIDDEN_IMPORTS = {"torch", "antenna", "win32com", "pywintypes", "scipy", "matplotlib", "pandas", "loguru"}
EXEMPT_DIRS = {"adapters", "legacy"}
#? 可維護性上限（CLAUDE.md 硬規則）：超過就拆檔／拆函式，不是調大數字。
MAX_MODULE_LINES = 400
MAX_FUNCTION_LINES = 60
#? 檔名要說出它做什麼——這幾個名字說的是「我不知道放哪」。
BANNED_MODULE_STEMS = {"utils", "util", "misc", "helpers", "common", "dedust", "tools", "stuff"}

#? Depot 守門（M12）：核心每個模組都要在下面**三張清單之一**（test_every_core_module_is_classified_for_depot_gate）。
#  DEPOT_ONLY：共享狀態只准經 `Depot`——不 import pathlib／shutil／glob／emforge.fs、不用 os.path／os.replace…、不 open(。
#  換後端＝實作一個 `Depot`；只要有一個模組偷偷開檔，那個保證就是假的。
DEPOT_ONLY_MODULES = (
    "__init__.py", "__main__.py", "specs.py", "netid.py", "events.py", "profiles.py", "heartbeat.py",
    "client.py", "client_view.py", "submissions.py", "runtime/inbox.py",
    "platform/__init__.py", "platform/service.py", "platform/wire.py",
    "platform/transport.py", "platform/http_server.py", "cli/platform.py",
    "db.py", "batches.py", "ledger.py", "queue.py", "report.py",
    "runtime/__init__.py", "runtime/collect.py", "runtime/notarize.py", "runtime/dispatch.py", "runtime/reconcile.py",
    "runtime/schedule.py",
    "worker/__init__.py", "worker/batch.py", "worker/gate.py", "worker/fuse.py",
    "device/__init__.py", "device/states.py", "device/reference.py", "device/mcp_server.py", "device/mcp_client.py",
    "cli/__init__.py", "cli/__main__.py", "cli/control.py", "cli/show.py", "cli/verdict.py", "cli/loops.py",
    "strategies/__init__.py", "strategies/blind.py", "strategies/top_k_flip.py",
)
#? DEPOT_ONLY_PARTIAL：可以用本機路徑（pathlib）——但只給「程式碼／設定根」（registry.py、strategies/、策略 workdir、
#  本機工作目錄）；一樣不准 import emforge.fs 或自己 open(。值＝為什麼需要本機路徑。
DEPOT_ONLY_PARTIAL = {
    "model.py": "canonical_json 的 _json_default 要認得 Path（序列化用，不碰檔）",
    "strategy.py": "策略碼與 registry.py 用本機路徑 importlib／runpy 載入；_proposals.npz 是同機子行程交接",
    "runtime/core.py": "root＝本機程式碼根（registry.py／策略 workdir）",
    "worker/loop.py": "本機 registry.py 與工作目錄根",
    "worker/guard.py": "看門狗（不碰檔）",
    "device/estop.py": "第三層急停是本機檔 <root>/ESTOP（NAS 斷線也要擋得住）",
    "device/limits.py": "部署設定 <root>/limits.json 是本機檔（每台自己的上限，不經 depot）",
    "device/instrument.py": "root＝本機程式碼根（前置檢查、急停本機層）；本機工作目錄 WorkDir",
    "cli/base.py": "--root 是本機路徑",
    "cli/device.py": "device-simulate 的 `--bits @file` 讀本機檔（使用者給的 pattern 檔）",
    "cli/setup.py": "init 寫本機 registry.py／strategies/；import-legacy 的舊樹是本機路徑",
    "testing.py": "假根建本機 registry.py",
    "_version.py": "git describe 看本機 repo",
}
#? LOCAL_LAYER：本機層／後端本體，本來就認得檔案系統。值＝理由；新增模組不准隨手放這裡。
LOCAL_LAYER = {
    "paths.py": "磁碟名／本機路徑的唯一來源（回 key 與 Path 兩種）",
    "fs.py": "FileDepot 的實作原語＋本機工作目錄清掃",
    "depot/__init__.py": "後端本體", "depot/base.py": "後端本體", "depot/file.py": "後端本體",
    "depot/http.py": "遠端後端本體",
    "depot/memory.py": "後端本體", "depot/uri.py": "後端本體",
    "doctor.py": "本機體檢：root 探針、C 槽、ansysedt 行程",
    "worker/workdir.py": "本機工作目錄生命週期（I-1 是本機碟事故）",
}
#? 儀器層依賴方向（M13）：device/* 只可 import worker 的 leaf（guard／gate／workdir）；worker 的 leaf 不可 import device；
#  只有 worker/batch.py、worker/loop.py（與 runtime、cli）可以 import device。
WORKER_LEAF = {"worker/gate.py", "worker/guard.py", "worker/fuse.py", "worker/workdir.py"}
DEVICE_MAY_IMPORT_FROM_WORKER = {"worker.guard", "worker.gate", "worker.workdir"}
FS_IMPORTS = {"pathlib", "shutil", "glob", "tempfile"}
#? optional extra（`emforge[mcp]`）：這些套件只准在下面的模組、而且只准在**函式內** import——核心 import 期零 mcp，
#  沒裝 mcp 的機器（開發機、runtime 機）照常跑 worker／runtime／CLI。
OPTIONAL_EXTRA_IMPORTS = {"mcp", "uvicorn", "starlette", "httpx2", "anyio"}
OPTIONAL_EXTRA_MODULES = {"device/mcp_server.py", "device/mcp_client.py"}
#? `os.path` 也算（getmtime／exists 都在裡面）。
FS_OS_ATTRS = {"path", "replace", "utime", "open", "unlink", "remove", "rename", "makedirs", "scandir",
               "listdir", "mkdir", "stat", "fsync"}


def _core_modules():
    for p in sorted(PKG.rglob("*.py")):
        rel = p.relative_to(PKG).parts
        if rel[0] in EXEMPT_DIRS:
            continue
        yield p


def _all_modules():
    return sorted(PKG.rglob("*.py"))


def test_package_importable_and_has_version():
    assert isinstance(emforge.__version__, str) and emforge.__version__


def test_core_modules_never_import_torch_or_antenna():
    """核心零領域依賴：用 ast 掃 import 節點（靜態、不執行、快）。"""
    offenders = []
    for p in _core_modules():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                if n.split(".")[0] in FORBIDDEN_IMPORTS:
                    offenders.append(f"{p.relative_to(PKG)}: import {n}")
    assert not offenders, "\n".join(offenders)


def test_no_module_exceeds_400_lines():
    too_long = [
        f"{p.relative_to(PKG)}: {n} 行"
        for p in _all_modules()
        if (n := len(p.read_text(encoding="utf-8").splitlines())) > MAX_MODULE_LINES
    ]
    assert not too_long, "拆檔，不是調大上限：\n" + "\n".join(too_long)


def test_no_function_exceeds_60_lines():
    too_long = []
    for p in _all_modules():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                n = node.end_lineno - node.lineno + 1
                if n > MAX_FUNCTION_LINES:
                    too_long.append(f"{p.relative_to(PKG)}:{node.lineno} {node.name} ({n} 行)")
    assert not too_long, "拆函式，不是調大上限：\n" + "\n".join(too_long)


def test_no_module_named_utils_misc_or_dedust():
    bad = [str(p.relative_to(PKG)) for p in _all_modules() if p.stem in BANNED_MODULE_STEMS]
    assert not bad, f"檔名要說出它做什麼：{bad}"


def _imports_fs(node) -> bool:
    """`from .. import fs`／`from .fs import x`／`from emforge.fs import x`／`import emforge.fs` 都算。"""
    if isinstance(node, ast.Import):
        return any(a.name in ("emforge.fs",) for a in node.names)
    mod = node.module or ""
    if node.level and (mod == "fs" or mod.startswith("fs.")):
        return True
    if node.level and mod == "" and any(a.name == "fs" for a in node.names):
        return True
    return mod == "emforge.fs" or (mod == "emforge" and any(a.name == "fs" for a in node.names))


def _fs_offenders(path, *, full: bool):
    """回這個模組裡「還認得檔案系統」的證據；`full=False` 只查 fs 原語與 `open(`。"""
    bad = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if _imports_fs(node):
                bad.append(f"L{node.lineno}: import 了 emforge.fs（fs 原語）")
            elif full:
                mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                        else ([node.module] if node.module and not node.level else []))
                for m in mods:
                    if m.split(".")[0] in FS_IMPORTS:
                        bad.append(f"L{node.lineno}: import {m}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            bad.append(f"L{node.lineno}: open(")
        elif full and isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "os" and node.attr in FS_OS_ATTRS:
            bad.append(f"L{node.lineno}: os.{node.attr}")
    return [f"{path.name} {b}" for b in bad]


def test_coordination_modules_touch_state_only_via_depot():
    """M12 的紅線：DEPOT_ONLY 模組不准 import pathlib／shutil／glob／emforge.fs，不准 os.path／os.replace…／open(；
    PARTIAL 模組可用本機路徑但不准 emforge.fs／open(。換後端＝實作一個 `Depot`；只要有一個模組偷偷開檔，那個保證就是假的。"""
    offenders = []
    for name in DEPOT_ONLY_MODULES:
        offenders += _fs_offenders(PKG / name, full=True)
    for name in DEPOT_ONLY_PARTIAL:
        offenders += _fs_offenders(PKG / name, full=False)
    assert not offenders, "共享狀態只准經 Depot：\n" + "\n".join(offenders)


def _package_refs(path) -> set:
    """這個模組 import 到的 emforge 內部模組（去掉 `emforge.` 前綴；相對 import 解析成同樣形式）。"""
    refs = set()
    rel = path.relative_to(PKG).parts[:-1]
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            refs |= {a.name[len("emforge."):] for a in node.names if a.name.startswith("emforge.")}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = list(rel[:len(rel) - (node.level - 1)]) if node.level > 1 else list(rel)
                mod = ".".join(base + ([node.module] if node.module else []))
            elif (node.module or "").startswith("emforge"):
                mod = (node.module or "")[len("emforge"):].lstrip(".")
            else:
                continue
            refs.add(mod)
            refs |= {f"{mod}.{a.name}".strip(".") for a in node.names}
    return refs


def test_device_layer_dependency_direction():
    """M13：device/* 對 worker 只能碰 leaf；worker 的 leaf 不准回頭 import device（否則儀器層與 worker 互相纏住）。"""
    offenders = []
    for p in _core_modules():
        rel = p.relative_to(PKG).as_posix()
        refs = _package_refs(p)
        if rel.startswith("device/"):
            bad = {r for r in refs if r == "worker" or r.startswith("worker.")} - DEVICE_MAY_IMPORT_FROM_WORKER
            bad = {r for r in bad if not any(r.startswith(ok + ".") for ok in DEVICE_MAY_IMPORT_FROM_WORKER)}
            offenders += [f"{rel}: import {r}" for r in sorted(bad)]
        if rel in WORKER_LEAF:
            offenders += [f"{rel}: import {r}" for r in sorted(refs) if r == "device" or r.startswith("device.")]
    assert not offenders, "儀器層依賴方向：\n" + "\n".join(offenders)


def test_mcp_only_imported_inside_functions_of_mcp_server():
    """M15：`mcp`／`uvicorn`／`starlette`／`httpx2`／`anyio` 只准出現在 OPTIONAL_EXTRA_MODULES，且只准在函式內 import。"""
    offenders = []
    for p in _all_modules():
        rel = p.relative_to(PKG).as_posix()
        tree = ast.parse(p.read_text(encoding="utf-8"))
        inside = {id(n) for f in ast.walk(tree) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                  for n in ast.walk(f) if isinstance(n, (ast.Import, ast.ImportFrom))}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            hit = [n for n in names if n.split(".")[0] in OPTIONAL_EXTRA_IMPORTS]
            if not hit:
                continue
            if rel not in OPTIONAL_EXTRA_MODULES:
                offenders.append(f"{rel}:L{node.lineno}: import {hit}（只准在 {sorted(OPTIONAL_EXTRA_MODULES)}）")
            elif id(node) not in inside:
                offenders.append(f"{rel}:L{node.lineno}: 模組層 import {hit}（要放進函式內）")
    assert not offenders, "\n".join(offenders)


def test_every_core_module_is_classified_for_depot_gate():
    """新模組必須自己表態：DEPOT_ONLY／PARTIAL（附理由）／LOCAL_LAYER（附理由）三選一，不能默默漏掉守門。"""
    listed = set(DEPOT_ONLY_MODULES) | set(DEPOT_ONLY_PARTIAL) | set(LOCAL_LAYER)
    actual = {p.relative_to(PKG).as_posix() for p in _core_modules()}
    missing, stale = sorted(actual - listed), sorted(listed - actual)
    assert not missing, f"這些模組沒被歸類（DEPOT_ONLY／PARTIAL／LOCAL_LAYER）：{missing}"
    assert not stale, f"清單裡有不存在的模組：{stale}"
    overlap = [n for n in listed if sum(n in c for c in (DEPOT_ONLY_MODULES, DEPOT_ONLY_PARTIAL, LOCAL_LAYER)) > 1]
    assert not overlap, f"一個模組只能在一張清單：{overlap}"


def test_cli_version_returns_zero(capsys):
    from emforge.cli import main
    assert main(["version"]) == 0
    out = capsys.readouterr().out
    assert emforge.__version__ in out and "emforge" in out


def test_naming_doc_mentions_reserved_names():
    """docs/naming.md 是命名規範的人讀版；保留字在程式（paths.RESERVED_STRATEGY_NAMES）與文件要一致。"""
    doc = (DOCS / "naming.md").read_text(encoding="utf-8")
    for name in sorted(paths.RESERVED_STRATEGY_NAMES) + ["blind"]:
        assert f"`{name}`" in doc, f"docs/naming.md 沒提到保留字 {name}"
    for word in ("snake_case", "CapWords", "EMFORGE_", "#!", "#?"):
        assert word in doc


def test_suite_is_pinned_against_live_machine_state_and_env(root):
    """檢查 #23／#25：測試不能耦合「這台機器現在的狀態」（ansysedt 在跑、磁碟剩多少）與環境變數（EMFORGE_DEPOT 設了
    會把假資料灌進共享庫）。conftest 的 autouse 把體檢釘成固定值、把整組 EMFORGE_* 拿掉。"""
    import os
    from emforge import doctor
    assert doctor.ansysedt_running() is False and doctor._free_gb(root) == 100.0
    for name in ("EMFORGE_ROOT", "EMFORGE_DEPOT", "EMFORGE_WORK", "EMFORGE_MACHINE", "EMFORGE_DEVICE_TOKEN",
                 "EMFORGE_MCP_HOST", "EMFORGE_MCP_PORT"):
        assert name not in os.environ, name
