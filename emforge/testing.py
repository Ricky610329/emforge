"""emforge/testing.py — 假件：`FakeSimulator`、`fake_measure`、`FAKE_PROFILE`／`FAKE_SPEC`／`FAKE_TARGETS`。

不叫 Mock：這是一個**確定性的假儀器**，同 bits 同響應、不同 bits 不同響應，可指定哪些 id 失敗／卡住。
使用者寫策略的測試也可以 import 這裡。
"""
import threading
import time
from pathlib import Path

import numpy as np

from . import paths, profiles, specs
from .depot import open_depot
from .model import Profile, SimResult, Simulator, Spec, record_id

GEOM_VER = "fake1"
FAKE_TARGETS = {"L1": {"center": -10.0, "side": -3.0}, "L2": {"center": 0.0, "side": -20.0}}

_fixed = np.zeros((8, 8), bool)
_fixed[:2, :2] = True
FAKE_PROFILE = Profile(name="fake_f1", simulator="emforge.testing:FakeSimulator", geom_ver=GEOM_VER, kwargs={},
                       shape=(8, 8), labels=("L1", "L2"), n_points=17, fixed_on=_fixed,
                       measure="fake_m", spec="fake_v1", timeout_s=5)
FAKE_SPEC = Spec(name="fake_v1", labels=("L1", "L2"), measure="fake_m",
                 axes=("m1", "m2", "m3", "m4"), offsets=(0.0, 0.0, 0.0, 0.0))


class FakeFailure(RuntimeError):
    """指定 id 的模擬失敗（模仿 COM 偶發例外）。"""


class FakeKilled(RuntimeError):
    """卡住的模擬被 kill() 中斷（模仿看門狗殺 HFSS 後 COM 呼叫拋錯）。"""


class FakeSimulator(Simulator):
    geom_ver = GEOM_VER
    labels = ("L1", "L2")

    def __init__(self, *, workdir: str, profile: Profile, fail_ids=(), hang_ids=(), delay_s: float = 0.0):
        super().__init__(workdir=workdir, profile=profile)
        self.fail_ids, self.hang_ids, self.delay_s = set(fail_ids), set(hang_ids), delay_s
        self.calls = {"open": 0, "simulate": 0, "kill": 0, "close": 0}
        self._killed = threading.Event()

    def open(self) -> None:
        self.calls["open"] += 1

    def simulate(self, bits) -> SimResult:
        self.calls["simulate"] += 1
        rid = record_id(bits, self.profile.name)
        if rid in self.fail_ids:
            raise FakeFailure(f"fake failure for {rid}")
        if rid in self.hang_ids:
            self._killed.wait()          # 卡到有人 kill()
            self._killed.clear()
            raise FakeKilled(f"killed while simulating {rid}")
        if self.delay_s:
            time.sleep(self.delay_s)
        rng = np.random.default_rng(int(rid, 16) % (2 ** 32))
        response = rng.normal(-10.0, 5.0, (len(self.profile.labels), self.profile.n_points)).astype(np.float32)
        return SimResult(response=response, time_s=float(self.delay_s), extra={})

    def kill(self) -> None:
        self.calls["kill"] += 1
        self._killed.set()

    def close(self) -> None:
        self.calls["close"] += 1


def fake_measure(response, labels, targets) -> dict:
    """四軸假尺：帶內 idx 5-11、帶外兩側。形狀與真尺（m1..m4）相同，數字無物理意義。"""
    r = np.asarray(response, np.float32)
    band = slice(5, 12)
    t1, t2 = targets["L1"], targets["L2"]
    return {"m1": float(t1["center"] - r[0, band].max()),
            "m2": float(r[1, band].min() - t2["center"]),
            "m3": float(t1["side"] - r[0, :5].max()),
            "m4": float(t1["side"] - r[0, 12:].max())}


def register_fakes() -> None:
    """把假尺／假 spec／假 profile 註冊進去（可重跑）。"""
    specs.register_measure("fake_m", fake_measure, labels=FAKE_PROFILE.labels, targets=FAKE_TARGETS)
    specs.register_spec(FAKE_SPEC)
    profiles.register_profile(FAKE_PROFILE)


def run_all_jobs(root, machine_tag: str = "216", sim_factory=None, max_jobs: int = 50, **loop_kw) -> int:
    """行程內假 worker：把佇列裡所有可認領的 job 跑完（worker_loop once 迴圈）。回跑掉的 job 數。"""
    from .queue import Queue
    from .worker.loop import worker_loop
    q = Queue(root)
    loop_kw.setdefault("sleep", lambda s: None)
    n = 0
    while n < max_jobs and any(q.state(j.store) == "queued" for j in q.list()):
        worker_loop(root, machine_tag, once=True, work_root=Path(root) / "_work", sim_factory=sim_factory, **loop_kw)
        n += 1
    return n


def make_fake_root(root) -> Path:
    """建一個可用的假根目錄：佈局目錄＋`registry.py`（子行程與 worker 會執行它）＋本行程也註冊。"""
    root = Path(root)
    open_depot(root).ensure_prefixes(paths.layout_prefixes())
    paths.user_strategies_dir(root).mkdir(parents=True, exist_ok=True)
    paths.registry_py(root).write_text("from emforge.testing import register_fakes\nregister_fakes()\n", encoding="utf-8")
    register_fakes()
    return root
