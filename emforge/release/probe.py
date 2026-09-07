"""隔離 fake 端到端探針；驗證版本時不讀正式資料、不開真模擬器。"""
import tempfile
import numpy as np
from emforge import paths, testing, strategy
from emforge.client import Client
from emforge.depot import FileDepot
from emforge.platform.service import Platform
from emforge.platform.http_server import serving
from emforge.runtime.core import Runtime
from emforge.runtime import collect

def main():
    with tempfile.TemporaryDirectory(prefix="emforge_release_") as root:
        testing.make_fake_root(root)
        FileDepot(root).put_bytes(paths.strategies_yaml("fake_f1"),
                           b"profile: fake_f1\nstrategies:\n- {name: anneal, kind: inbox, batch: 1, prio: 3}\n")
        rt = Runtime(root, "fake_f1", propose_fn=strategy.propose_in_process)
        with serving(Platform(rt.depot)) as url:
            client = Client(url, "fake_f1", "anneal", run_id="release_probe")
            bits = np.ones(testing.FAKE_PROFILE.shape, bool)
            sid = client.submit([bits], request_id="one")
            rt.tick()
            testing.run_all_jobs(root)
            collect.collect(rt)
            assert client.wait(sid, timeout_s=2)["state"] == "completed"
            assert len(client.results(sid)) == 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
