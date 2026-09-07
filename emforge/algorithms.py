"""不可變算法程式碼包：固定內容、入口與相依模組。"""
import hashlib
import re

from .model import canonical_json

MODULE = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$")
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def check_source_path(name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise ValueError("程式碼路徑不合法")
    segments = name.split("/")
    if any(s in ("", ".", "..") or s.rstrip(" .") != s or s.split(".")[0].upper() in RESERVED for s in segments):
        raise ValueError("程式碼路徑不可逃出工作目錄或使用保留名稱")
    return name


def package(files, entrypoint, requires=(), checkpoint_schema=None):
    if not isinstance(files, dict) or not files or len(files) > 1000:
        raise ValueError("程式碼包需含 1–1000 個文字檔")
    seen = set()
    for name, content in files.items():
        check_source_path(name)
        if name.casefold() in seen:
            raise ValueError("程式碼包路徑不可有大小寫衝突")
        seen.add(name.casefold())
        if not isinstance(content, str):
            raise ValueError("第一版程式碼包只接受文字檔")
        if name.endswith(".py"):
            compile(content, name, "exec")
    if entrypoint not in files or not entrypoint.endswith(".py"):
        raise ValueError("entrypoint 必須是包內 Python 檔")
    if any(not isinstance(r, str) or not MODULE.fullmatch(r) for r in requires):
        raise ValueError("requires 為可 import 的模組名列表")
    doc = {"files": files, "entrypoint": entrypoint, "requires": sorted(set(requires)),
           "checkpoint_schema": checkpoint_schema}
    raw = canonical_json(doc).encode("utf-8")
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("程式碼包上限 16 MiB")
    return "pkg_" + hashlib.sha256(raw).hexdigest(), doc
