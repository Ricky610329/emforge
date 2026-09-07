"""emforge/worker/guard.py — 看門狗、處決線、開模擬器重試。

#! COM 呼叫可以「無例外地永遠不回來」（2026-07-10）：每一個會碰模擬器的環節都要有處決線——
#  逾時就 kill()，讓卡住的呼叫拋錯、走 error 路徑。開啟／重開也會卡死（218，2026-07-11）→ 帶看門狗、三試。
"""
import threading
import time


class WatchdogTimeout(Exception):
    """呼叫在 timeout_s 內沒回來，已 kill；原例外在 __cause__。"""


class Aborted(WatchdogTimeout):
    """`abort_if` 變真（e-stop）而被 kill——不是逾時、不重開模擬器。"""


class SimulatorOpenFailed(Exception):
    """連續 attempts 次開不起來——疑似機器壞死，這批判死。"""


class Watchdog:
    """`with Watchdog(t, on_timeout):`——區塊內超過 t 秒就呼叫 on_timeout（通常是 sim.kill），`fired` 記錄有沒有觸發；
    給 `abort_if` 就每 poll_s 查一次，真了也 on_timeout（`aborted` 記錄是哪一種）。"""

    def __init__(self, timeout_s: float, on_timeout, *, abort_if=None, poll_s: float = 1.0):
        self.timeout_s, self.on_timeout, self.fired, self.aborted = float(timeout_s), on_timeout, False, False
        self._abort_if, self._poll_s = abort_if, float(poll_s)
        self._halt = threading.Event()
        self._thread = None

    def _fire(self, aborted: bool = False) -> None:
        self.fired, self.aborted = True, aborted
        try:
            self.on_timeout()
        except Exception:  # noqa: BLE001 — 殺不掉也不能讓看門狗執行緒炸
            pass

    def _watch(self) -> None:
        deadline = time.monotonic() + self.timeout_s
        step = self._poll_s if self._abort_if is not None else self.timeout_s
        while not self._halt.wait(min(step, max(0.0, deadline - time.monotonic()))):
            if self._abort_if is not None and self._abort_if():
                return self._fire(aborted=True)
            if time.monotonic() >= deadline:
                return self._fire()

    def __enter__(self):
        self._thread = threading.Thread(target=self._watch, daemon=True, name="emforge-watchdog")
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._halt.set()


def guarded_call(fn, timeout_s: float, on_timeout, *, abort_if=None, poll_s: float = 1.0):
    """呼叫 fn()；逾時由 on_timeout 讓它拋錯，並包成 WatchdogTimeout（abort_if 觸發＝Aborted）。沒逾時的例外原樣拋。"""
    with Watchdog(timeout_s, on_timeout, abort_if=abort_if, poll_s=poll_s) as wd:
        try:
            return fn()
        except Exception as e:
            if wd.aborted:
                raise Aborted(f"被中止（abort_if）已殺：{e}") from e
            if wd.fired:
                raise WatchdogTimeout(f"{timeout_s:.0f}s 逾時已殺：{e}") from e
            raise


CLOSE_TIMEOUT_S = 60.0   #? quit() 的處決線：COM 呼叫可以無例外地永遠不回來（檢查 #18）


def close_quiet(sim, timeout_s: float | None = None) -> None:
    """關模擬器帶處決線：逾時就 kill 再往下走；任何例外吞掉（關不掉就殺，殺不掉也不能讓收尾炸）。
    #! 檢查 #18（2026-09-07）：以前只有 open／simulate 有看門狗，close 裸奔——quit() 卡住＝worker 主迴圈永遠卡在 finally、
    #  simulate_once 的租約永不釋放（fleet 還顯示線上）。"""
    try:
        guarded_call(sim.close, timeout_s or CLOSE_TIMEOUT_S, sim.kill)
    except Exception:  # noqa: BLE001
        pass


def open_with_retries(sim, *, attempts: int = 3, timeout_s: float = 300.0, sleep=time.sleep,
                      retry_wait_s: float = 15.0, fatal: tuple = ()) -> None:
    """開模擬器最多 attempts 次，每次帶看門狗；失敗就 kill、等 retry_wait_s 再試；用盡拋 SimulatorOpenFailed。
    `fatal`＝不是「機器卡住」的拒絕（急停、前置檢查）：原樣立刻拋、不 kill、不重試。"""
    last = None
    for i in range(attempts):
        try:
            guarded_call(sim.open, timeout_s, sim.kill)
            return
        except fatal:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            try:
                sim.kill()
            except Exception:  # noqa: BLE001
                pass
            if i < attempts - 1:
                sleep(retry_wait_s)
    raise SimulatorOpenFailed(f"模擬器連續 {attempts} 次開不起來：{last}")
