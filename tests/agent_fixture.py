"""Isolated HTTP platform for the real Pi/MCP integration tests; no HFSS/NAS."""
import json
import os
from pathlib import Path
import sys

from emforge import _version, doctor, testing
from emforge.platform.http_server import serving
from emforge.platform.service import Platform
from emforge.runtime import collect
from tests.test_client import setup


def main():
    for key in ("EMFORGE_ROOT", "EMFORGE_DEPOT", "EMFORGE_WORK", "EMFORGE_MACHINE",
                "EMFORGE_DEVICE_TOKEN", "EMFORGE_PLATFORM_TOKEN"):
        os.environ.pop(key, None)
    doctor.ansysedt_running = lambda: False
    doctor._free_gb = lambda p: 100.0
    _version.describe = lambda: "emforge=agent-fixture"
    rt = setup(Path(sys.argv[1]))

    class ImmediateFakePlatform(Platform):
        def submit(self, **params):
            sid = super().submit(**params)
            rt.tick()
            testing.run_all_jobs(rt.root)
            collect.collect(rt)
            return sid

    with serving(ImmediateFakePlatform(rt.depot), secret="integration-test-token") as endpoint:
        print(json.dumps({"endpoint": endpoint, "token": "integration-test-token"}), flush=True)
        sys.stdin.read()


if __name__ == "__main__":
    main()
