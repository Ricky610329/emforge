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
    "__init__.py", "__main__.py", "specs.py", "netid.py", "events.py", "profiles.py",
    "db.py", "batches.py", "ledger.py", "queue.py", "report.py",
    "runtime/__init__.py", "runtime/collect.py", "runtime/notarize.py", "runtime/dispatch.py", "runtime/reconcile.py",
    "runtime/schedule.py",
    "worker/__init__.py", "worker/batch.py", "worker/gate.py", "worker/fuse.py",
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
    "cli/base.py": "--root 是本機路徑",
    "cli/setup.py": "init 寫本機 registry.py／strategies/；import-legacy 的舊樹是本機路徑",
    "testing.py": "假根建本機 registry.py",
    "_version.py": "git describe 看本機 repo",
}
#? LOCAL_LAYER：本機層／後端本體，本來就認得檔案系統。值＝理由；新增模組不准隨手放這裡。
LOCAL_LAYER = {
    "paths.py": "磁碟名／本機路徑的唯一來源（回 key 與 Path 兩種）",
    "fs.py": "FileDepot 的實作原語＋本機工作目錄清掃",
    "depot/__init__.py": "後端本體", "depot/base.py": "後端本體", "depot/file.py": "後端本體",
    "depot/memory.py": "後端本體", "depot/uri.py": "後端本體",
    "doctor.py": "本機體檢：root 探針、C 槽、ansysedt 行程",
    "worker/workdir.py": "本機工作目錄生命週期（I-1 是本機碟事故）",
}
FS_IMPORTS = {"pathlib", "shutil", "glob", "tempfile"}
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
