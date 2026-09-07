"""既有 Python 環境檢查與固定程式碼包展開。"""
import json
import os
import subprocess
from pathlib import Path

from .. import algorithms, paths


def inspect_environment(python, requires):
    if not Path(python).is_file():
        raise ValueError(f"Python 不存在：{python}")
    code = ("import sys,json,importlib.util; names=json.loads(sys.argv[1]); "
            "missing=[n for n in names if importlib.util.find_spec(n) is None]; "
            "print(json.dumps(dict(python=sys.version,executable=sys.executable,missing=missing)))")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run([str(python), "-c", code, json.dumps(["emforge", *requires])],
                            capture_output=True, text=True, timeout=20, creationflags=flags)
    if result.returncode:
        raise ValueError("Python 環境檢查失敗：" + result.stderr[-2000:])
    report = json.loads(result.stdout)
    if report["missing"]:
        raise ValueError("缺少相依模組：" + ", ".join(report["missing"]))
    return report


def prepare(root, identity, version, package):
    actual, _ = algorithms.package(**package)
    if actual != version:
        raise ValueError("程式碼包雜湊不符")
    target = paths.runner_source(root, identity["run_id"])
    target.mkdir(parents=True, exist_ok=True)
    for name, content in package["files"].items():
        p = paths.runner_source_file(root, identity["run_id"], name)
        if p.exists() and p.read_text(encoding="utf-8") != content:
            raise ValueError("既有執行目錄有不同程式碼，不覆寫")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    paths.runner_work(root, identity["run_id"]).mkdir(parents=True, exist_ok=True)
    return paths.runner_source_file(root, identity["run_id"], package["entrypoint"])
