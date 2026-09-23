"""穩定 bootstrap：停止派工、換行程、啟動失敗回退；不修改量測資料。"""
import os
from pathlib import Path
import time
import uuid
from .. import paths
from ..depot import FileDepot, open_depot
from ..platform.transport import RemotePlatform
from ..runner.process import OwnedProcess, process_identity
from .store import ReleaseStore

def resume_pending_request(state: dict) -> dict:
    """draining／stopping 中死掉：要求已記為 seen 但沒套用——清掉 seen 讓重啟後再套用一次（不靜默丟；I-27）。"""
    if state.get("phase") in ("draining", "stopping") and state.get("target"):
        state["seen_request"] = None
    return state


class Supervisor:
    def __init__(self, releases_root, data_root, profile, *, depot=None, host="127.0.0.1", port=8766,
                 startup_s=30, drain_s=120, stop_s=90):
        self.store = ReleaseStore(releases_root)
        self.local = self.store.depot
        self.data_root, self.profile = Path(data_root).resolve(), profile
        self.depot = open_depot(depot) if depot else FileDepot(self.data_root)
        self.host, self.port = host, port
        self.startup_s, self.drain_s, self.stop_s = startup_s, drain_s, stop_s
        self.owner = "service_" + uuid.uuid4().hex
        self.process, self.output, self.launch = None, None, None
        self.state = resume_pending_request(self.local.get_json(paths.service_key("state"))
                                            or {"last_good": None, "seen_request": None})
        self._acquire()
        try:
            self._recover_previous()
            self.state.update(phase="idle", current=self.state.get("last_good"))
        except BaseException:
            self.local.release(paths.service_key("lock"), owner=self.owner)
            raise

    def _acquire(self):
        key = paths.service_key("lock")
        old = self.local.owner(key)
        self.previous_owner = old.get("owner") if old else None
        if old:
            alive = process_identity(old["pid"])
            if alive == old["birth"]:
                raise RuntimeError("此 releases-root 已有 supervisor")
            self.local.release(key, owner=old["owner"])
        payload = {"owner": self.owner, "pid": os.getpid(), "birth": process_identity(os.getpid())}
        if not self.local.claim(key, payload):
            raise RuntimeError("supervisor 鎖被占用")

    def _recover_previous(self):
        info = self.local.get_json(paths.service_key("child"))
        if info and process_identity(info["pid"]) == info["birth"]:
            raise RuntimeError("前一個平台子行程仍活著，拒絕重複啟動")
        if info:
            self._release_child_locks(info)
        for key in (paths.maintenance(self.profile), paths.runtime_stop(self.profile)):
            if not self.depot.exists(key):
                continue
            doc = self.depot.get_json(key)
            if not doc or doc.get("owner") != self.previous_owner:
                raise RuntimeError("現有維護／STOP 控制不屬於此 supervisor")
            self.depot.delete(key)

    def _save(self):
        self.local.put_json(paths.service_key("state"), self.state)

    def _control(self, key):
        old = self.depot.get_json(key)
        if self.depot.exists(key) and (not old or old.get("owner") != self.owner):
            raise RuntimeError("控制檔由其他操作者持有")
        self.depot.put_json(key, {"owner": self.owner})

    def _clear_controls(self):
        for key in (paths.maintenance(self.profile), paths.runtime_stop(self.profile)):
            doc = self.depot.get_json(key)
            if doc and doc.get("owner") == self.owner:
                self.depot.delete(key)

    def _launch(self, version):
        self.state["current"] = version
        try:
            self._spawn(version)
        except Exception as e:
            self._failed(f"啟動失敗：{type(e).__name__}: {e}")

    def _spawn(self, version):
        doc = self.store.verify(version, validated=True)
        self._control(paths.maintenance(self.profile))
        self.launch = "launch_" + uuid.uuid4().hex
        source = paths.release_source(self.store.root, version)
        env = dict(os.environ)
        env.update(PYTHONPATH=str(source), PYTHONIOENCODING="utf-8",
                   EMFORGE_SERVICE_ROOT=str(self.store.root), EMFORGE_SERVICE_DATA=str(self.data_root),
                   EMFORGE_SERVICE_DEPOT=self.depot.spec, EMFORGE_SERVICE_PROFILE=self.profile,
                   EMFORGE_SERVICE_HOST=self.host, EMFORGE_SERVICE_PORT=str(self.port),
                   EMFORGE_SERVICE_LAUNCH=self.launch, EMFORGE_RELEASE_VERSION=version)
        log = paths.service_stdout(self.store.root, self.launch)
        log.parent.mkdir(parents=True, exist_ok=True)
        self.output = log.open("wb")
        self.state.update(current=version, phase="starting", since=time.monotonic(), launch=self.launch)
        self._save()
        try:
            self.process = OwnedProcess(doc["python"], paths.release_host(self.store.root, version),
                                        source, env, self.output)
        except BaseException:
            self.output.close()
            self.output = None
            raise

    def _ready(self):
        info = self.local.get_json(paths.service_key("child"))
        if not info or info["launch"] != self.launch or not info["ready"]:
            return False
        host = "127.0.0.1" if self.host in ("0.0.0.0", "") else self.host
        url = f"http://{host}:{info['port']}"
        RemotePlatform(url, timeout_s=1).call("description", profile=self.profile)
        self.port = info["port"]
        self.state.update(phase="running", last_good=self.state["current"], url=url)
        self._clear_controls()
        self._save()
        return True

    def _finished(self):
        info = self.local.get_json(paths.service_key("child"))
        if info and info["launch"] == self.launch and info["pid"] == self.process.pid:
            self._release_child_locks(info)
        self.process.close()
        self.process = None
        self.output.close()
        self.output = None
        self._clear_controls()

    def _release_child_locks(self, info):
        """只回收已確認死亡子行程的鎖；舊 metadata 沒有 queue_owner 就不猜。"""
        self.depot.release(paths.runtime_lock(self.profile), owner=info["runtime_owner"])
        if info.get("queue_owner"):
            self.depot.release(paths.jobs_lock(), owner=info["queue_owner"])

    def _failed(self, reason):
        failed = self.state["current"]
        log = paths.service_stdout(self.store.root, self.launch) if self.launch else None
        detail = log.read_text(encoding="utf-8", errors="replace")[-16000:] if log and log.exists() else ""
        self.state.update(error=f"{reason}\n{detail}", phase="failed")
        previous = self.state.get("last_good")
        if previous and previous != failed:
            self.state["rolled_back_from"] = failed
            self._launch(previous)
        self._save()

    def _request(self):
        request = self.local.get_json(paths.service_key("request"))
        if not request or request["id"] == self.state.get("seen_request"):
            return None
        self.state["seen_request"] = request["id"]
        try:
            self.store.verify(request["version"], validated=True)
        except Exception as e:
            self.state["error"] = f"更新拒絕：{e}"
            self._save()
            return None
        return request["version"]

    def tick(self):
        owner = self.local.owner(paths.service_key("lock"))
        if not owner or owner.get("owner") != self.owner:
            raise RuntimeError("supervisor 已失去鎖")
        self.local.touch(paths.service_key("lock"))
        if self.process is None:
            target = self._request()
            if target or self.state["phase"] == "idle" and self.state.get("last_good"):
                self._launch(target or self.state["last_good"])
            return
        code = self.process.poll()
        if code is not None:
            phase, target = self.state["phase"], self.state.get("target")
            self._finished()
            if phase == "stopping":
                self._launch(target)
            else:
                self._failed(f"平台行程退出 {code}")
            return
        phase = self.state["phase"]
        if phase == "running":
            target = self._request()
            if target:
                self._control(paths.maintenance(self.profile))
                self.state.update(phase="draining", target=target, since=time.monotonic())
                self._save()
        elif phase == "starting":
            try:
                if self._ready():
                    return
            except (OSError, RuntimeError):
                pass
            if time.monotonic()-self.state["since"] > self.startup_s:
                self.process.terminate()
                self._finished()
                self._failed("新版本啟動逾時")
        elif phase in ("draining", "stopping"):
            self._advance_stop(phase)

    def _advance_stop(self, phase):
        elapsed = time.monotonic()-self.state["since"]
        if phase == "draining":
            status = self.depot.get_json(paths.status_json(self.profile)) or {}
            if (status.get("maintenance") or {}).get("owner") == self.owner:
                self._control(paths.runtime_stop(self.profile))
                self.state.update(phase="stopping", since=time.monotonic())
                self._save()
            elif elapsed > self.drain_s:
                self._clear_controls()
                self.state.update(phase="running", error="停止派工未確認，取消更新")
                self._save()
        elif elapsed > self.stop_s:
            self.process.terminate()  # 僅自己的行程樹；量測 worker 是獨立行程
            self._finished()
            self._launch(self.state["target"])

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
            self._finished()
        self.state["phase"] = "stopped"
        self._save()
        self.local.release(paths.service_key("lock"), owner=self.owner)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
