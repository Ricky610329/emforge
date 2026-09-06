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

#? 已抽象化的模組：共享狀態只准經 `Depot`（M12b）。M12c 加 queue、M12d 加 runtime／worker／cli——
#  所以這是一張**清單**，擴充就是往這裡加一個檔名。
DEPOT_ONLY_MODULES = ("db.py", "batches.py", "ledger.py", "events.py", "report.py", "profiles.py")
#? `strategy.py` 半套：策略碼與 registry.py 要用本機路徑 importlib／runpy 載入（程式碼不抽象），
#  但它一樣不准碰 fs 原語或自己 open(。
DEPOT_ONLY_PARTIAL = ("strategy.py",)
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
    """M12b 的紅線：已抽象化的模組不准 import pathlib／shutil／glob／emforge.fs，不准 os.path／os.replace…／open(。
    換後端＝實作一個 `Depot`；只要有一個模組偷偷開檔，那個保證就是假的。"""
    offenders = []
    for name in DEPOT_ONLY_MODULES:
        offenders += _fs_offenders(PKG / name, full=True)
    for name in DEPOT_ONLY_PARTIAL:
        offenders += _fs_offenders(PKG / name, full=False)
    assert not offenders, "共享狀態只准經 Depot：\n" + "\n".join(offenders)


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
