"""工作目錄所有權的真子行程回歸；只使用 FakeSimulator。"""
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from emforge import paths, queue, testing
from emforge.worker.loop import worker_loop
from emforge.worker.workdir import WorkDir
from tests.worker.conftest import make_batch, make_job


CHILD = """import sys
from pathlib import Path
from emforge import doctor, testing
from emforge.worker.loop import worker_loop
doctor.ansysedt_running = lambda: False
doctor._free_gb = lambda p: 100.0
class FakeBusy(testing.FakeSimulator):
    def simulate(self, bits):
        Path(sys.argv[3]).write_text('active', encoding='utf-8')
        sys.stdin.readline()
        return super().simulate(bits)
raise SystemExit(worker_loop(sys.argv[1], '216', work_root=sys.argv[2], once=True,
    sim_factory=lambda wd, p: FakeBusy(workdir=str(wd), profile=p)))
"""


def test_second_worker_refuses_before_sweeping_or_publishing_state(root, tmp_path):
    """回歸 I-1（2026-09-12）：防止第二個 worker 刪掉另一行程的使用中工作目錄。"""
    testing.make_fake_root(root)
    make_batch(root, "active", n=1)
    q = queue.Queue(root)
    q.add(make_job("active"))
    work = root / "work"
    active = work / "active" / "solver-input.txt"
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2]), "PYTHONIOENCODING": "utf-8"}
    with (tmp_path / "child.log").open("wb") as log:
        child = subprocess.Popen([sys.executable, str(script), str(root), str(work), str(active)],
                                 stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, env=env,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            end = time.monotonic() + 10
            while not active.exists() and child.poll() is None and time.monotonic() < end:
                time.sleep(.02)
            assert active.exists(), (tmp_path / "child.log").read_text(encoding="utf-8")
            before = q.depot.require_json(paths.device_state("216"))
            q.request_stop("216")
            with pytest.raises(RuntimeError, match="工作目錄"):
                worker_loop(root, "216", work_root=work, once=True)
            assert active.read_text(encoding="utf-8") == "active"
            assert q.depot.require_json(paths.device_state("216"))["pid"] == before["pid"] == child.pid
        finally:
            if child.poll() is None:
                child.stdin.write(b"finish\n")
                child.stdin.flush()
                child.wait(timeout=10)
            child.stdin.close()
    assert child.returncode == 0
    assert q.state("active") == "done"


def test_workdir_recovers_dead_owner_and_rejects_live_owner(tmp_path):
    """回歸 I-1（2026-09-12）：防止殘留 PID 鎖阻擋重啟，或活行程的鎖被誤破。"""
    work = WorkDir(tmp_path / "work")
    work.local.put_json(paths.worker_lock(), {"owner": "dead", "pid": os.getpid(), "birth": "old"})
    work.acquire()
    second = WorkDir(work.root)
    try:
        with pytest.raises(RuntimeError, match="工作目錄"):
            second.acquire()
        assert second.local.owner(paths.worker_lock())["owner"] == work.local.owner(paths.worker_lock())["owner"]
    finally:
        work.release()
    second.acquire()
    second.release()


@pytest.mark.skipif(os.name != "nt", reason="Windows 的行程 handle 語意")
def test_workdir_recovers_lock_of_exited_pid_whose_handle_is_still_open(tmp_path):
    """回歸 I-2（2026-09-23）：防止行程已結束、但別人仍持有其 handle（EDR／Process Explorer／父行程）時被判成活著，
    worker 永遠拒絕啟動。"""
    from emforge.runner.process import process_identity
    child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
    try:
        birth = process_identity(child.pid)
        assert birth is not None
        child.stdin.close()
        child.wait(timeout=30)
        assert process_identity(child.pid) is None, "已結束的行程（handle 仍開著）不算活著"
        work = WorkDir(tmp_path / "work")
        work.local.put_json(paths.worker_lock(), {"owner": "dead", "pid": child.pid, "birth": birth})
        work.acquire()
        assert work.local.owner(paths.worker_lock())["pid"] == os.getpid()
        work.release()
    finally:
        child.kill()


def test_workdir_recycles_empty_lock_after_grace_but_not_fresh(tmp_path):
    """回歸 I-2（2026-09-23）：防止藍屏／斷電留下的 0 byte worker.lock 永久擋住啟動——空 claim 要有寬限，超過才當殘骸。"""
    work = WorkDir(tmp_path / "work")
    lock_path = work.local.path(paths.worker_lock())
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_bytes(b"")
    with pytest.raises(RuntimeError, match="無法辨識"):
        work.acquire()                                   # 剛出現的空檔＝有人正在寫，不能搶
    old = time.time() - 120
    os.utime(lock_path, (old, old))
    work.acquire()
    assert work.local.owner(paths.worker_lock())["pid"] == os.getpid()
    work.release()


def test_worker_state_root_refuses_drive_root():
    with pytest.raises(ValueError, match="磁碟根"):
        paths.worker_state_root("C:/" if os.name == "nt" else "/")
