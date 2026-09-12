"""可替換的協調端行程：HTTP＋單 profile runtime，共享持久狀態。"""
import os
import threading
from emforge import paths
from emforge.depot import FileDepot
from emforge.model import now_iso
from emforge.platform.http_server import make_server
from emforge.platform.service import Platform
from emforge.runner.process import process_identity
from emforge.runtime.core import Runtime

def main():
    root = os.environ["EMFORGE_SERVICE_DATA"]
    profile = os.environ["EMFORGE_SERVICE_PROFILE"]
    local = FileDepot(os.environ["EMFORGE_SERVICE_ROOT"])
    rt = Runtime(root, profile, depot=os.environ["EMFORGE_SERVICE_DEPOT"])
    server = make_server(Platform(rt.depot), host=os.environ["EMFORGE_SERVICE_HOST"],
                         port=int(os.environ["EMFORGE_SERVICE_PORT"]), secret=os.environ.get("EMFORGE_PLATFORM_TOKEN"))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .1}, daemon=True)
    info = {"launch": os.environ["EMFORGE_SERVICE_LAUNCH"], "pid": os.getpid(),
            "birth": process_identity(os.getpid()), "runtime_owner": rt._lock_owner,
            "queue_owner": rt.queue._lock_owner, "ready": False,
            "port": server.server_port, "at": now_iso()}
    local.put_json(paths.service_key("child"), info)
    def ready():
        thread.start()
        local.put_json(paths.service_key("child"), {**info, "ready": True})
    try:
        return rt.run(ready=ready)
    finally:
        if thread.is_alive():
            server.shutdown()
            thread.join(timeout=3)
        server.server_close()

if __name__ == "__main__":
    raise SystemExit(main())
