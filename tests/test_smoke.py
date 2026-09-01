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


def test_cli_version_returns_zero(capsys):
    from emforge.cli import main
    assert main(["version"]) == 0
    out = capsys.readouterr().out
    assert emforge.__version__ in out and "emforge" in out


def test_naming_doc_mentions_reserved_names():
    """docs/naming.md 是命名規範的人讀版；保留字在程式（paths.RESERVED_STRATEGY_NAMES）與文件要一致。"""
    doc = (DOCS / "naming.md").read_text(encoding="utf-8")
    for name in paths.RESERVED_STRATEGY_NAMES:
        assert f"`{name}`" in doc, f"docs/naming.md 沒提到保留字 {name}"
    for word in ("snake_case", "CapWords", "EMFORGE_", "#!", "#?"):
        assert word in doc
