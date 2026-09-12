"""本機不可變程式碼快照；只用既有 Python，不安裝依賴。"""
import hashlib
import os
from pathlib import Path
import subprocess
import uuid
from .. import paths
from ..algorithms import check_source_path
from ..depot import FileDepot
from ..model import canonical_json

def source_files(source):
    source = Path(source).resolve()
    out = {}
    for prefix in ("emforge", "tests", "docs", "examples", "scripts"):
        folder = source / prefix
        if folder.is_symlink():
            raise ValueError("release 不接受 symlink")
        for p in sorted(folder.rglob("*")) if folder.exists() else []:
            if "__pycache__" in p.parts or p.suffix == ".pyc":
                continue
            if p.is_symlink():
                raise ValueError("release 不接受 symlink")
            if p.is_file():
                name = check_source_path(p.relative_to(source).as_posix())
                out[name] = p.read_bytes()
    for name in ("pyproject.toml", "README.md", "CLAUDE.md", "AGENTS.md"):
        p = source / name
        if p.is_symlink():
            raise ValueError("release 不接受 symlink")
        if p.is_file():
            out[name] = p.read_bytes()
    if "emforge/__init__.py" not in out or not any(n.startswith("tests/") for n in out):
        raise ValueError("release 必須包含 emforge 與 tests")
    return out

def digest(files, python):
    hashes = {n: hashlib.sha256(data).hexdigest() for n, data in files.items()}
    version = "rel_" + hashlib.sha256(canonical_json([hashes, python]).encode()).hexdigest()
    return version, hashes

class ReleaseStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.depot = FileDepot(self.root)

    def stage(self, source, python):
        python = str(Path(python).resolve())
        files = source_files(source)
        version, hashes = digest(files, python)
        with self.depot.lock(paths.service_key("release_lock"), owner=uuid.uuid4().hex):
            old = self.depot.get_json(paths.release_manifest(version))
            if old:
                self.verify(version)
                return version
            for name, data in files.items():
                target = paths.release_source_file(self.root, version, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            self.depot.put_json(paths.release_manifest(version),
                                {"version": version, "python": python, "hashes": hashes, "valid": False})
        return version

    def verify(self, version, validated=False):
        doc = self.depot.require_json(paths.release_manifest(version))
        files = source_files(paths.release_source(self.root, version))
        actual, hashes = digest(files, doc["python"])
        if actual != version or hashes != doc["hashes"]:
            raise ValueError("release hash 不符；重新 stage，不能原地改已發布版本")
        if validated and not doc["valid"]:
            raise ValueError("release 尚未通過驗證")
        return doc

    def validate(self, version, timeout_s=600):
        doc = self.verify(version)
        source = paths.release_source(self.root, version)
        env = {k: v for k, v in os.environ.items() if not k.startswith("EMFORGE_") or k == "EMFORGE_ANTENNA_REPO"}
        env.update(PYTHONPATH=str(source), PYTHONIOENCODING="utf-8", EMFORGE_HFSS_TESTS="0")
        commands = [("pytest", "-o", "addopts=", "-q"), ("pyflakes", "emforge", "tests"),
                    ("emforge.release.probe",)]
        results = []
        for command in commands:
            try:
                out = subprocess.run([doc["python"], "-m", *command], cwd=source, env=env,
                                     capture_output=True, text=True, encoding="utf-8", errors="replace",
                                     timeout=timeout_s, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                results.append({"command": list(command), "exit_code": out.returncode,
                                "output": (out.stdout+out.stderr)[-32000:]})
            except (OSError, subprocess.TimeoutExpired) as e:
                results.append({"command": list(command), "exit_code": -1, "output": str(e)})
            if results[-1]["exit_code"]:
                break
        self.verify(version)
        doc.update(valid=len(results) == len(commands) and all(r["exit_code"] == 0 for r in results), checks=results)
        self.depot.put_json(paths.release_manifest(version), doc)
        return doc

    def request(self, version):
        self.verify(version, validated=True)
        request = {"id": uuid.uuid4().hex, "version": version}
        self.depot.put_json(paths.service_key("request"), request)
        return request
