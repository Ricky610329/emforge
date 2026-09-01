"""emforge/worker/guard.py — 看門狗、處決線、開模擬器重試。

#! COM 呼叫可以「無例外地永遠不回來」（2026-07-10）：每一個會碰模擬器的環節都要有處決線——
#  逾時就 kill()，讓卡住的呼叫拋錯、走 error 路徑。開啟／重開也會卡死（218，2026-07-11）→ 帶看門狗、三試。
"""
import threading
import time


class WatchdogTimeout(Exception):
    """呼叫在 timeout_s 內沒回來，已 kill；原例外在 __cause__。"""


class SimulatorOpenFailed(Exception):
    """連續 attempts 次開不起來——疑似機器壞死，這批判死。"""


class Watchdog:
    """`with Watchdog(t, on_timeout):`——區塊內超過 t 秒就呼叫 on_timeout（通常是 sim.kill），`fired` 記錄有沒有觸發。"""

    def __init__(self, timeout_s: float, on_timeout):
        self.timeout_s, self.on_timeout, self.fired = float(timeout_s), on_timeout, False
        self._timer = None

    def _fire(self) -> None:
        self.fired = True
        try:
            self.on_timeout()
        except Exception:  # noqa: BLE001 — 殺不掉也不能讓看門狗執行緒炸
            pass

    def __enter__(self):
        self._timer = threading.Timer(self.timeout_s, self._fire)
        self._timer.daemon = True
        self._timer.start()
        return self

    def __exit__(self, *exc):
        self._timer.cancel()


def guarded_call(fn, timeout_s: float, on_timeout):
    """呼叫 fn()；逾時由 on_timeout 讓它拋錯，並包成 WatchdogTimeout。沒逾時的例外原樣拋。"""
    with Watchdog(timeout_s, on_timeout) as wd:
        try:
            return fn()
        except Exception as e:
            if wd.fired:
                raise WatchdogTimeout(f"{timeout_s:.0f}s 逾時已殺：{e}") from e
            raise


def open_with_retries(sim, *, attempts: int = 3, timeout_s: float = 300.0, sleep=time.sleep,
                      retry_wait_s: float = 15.0) -> None:
    """開模擬器最多 attempts 次，每次帶看門狗；失敗就 kill、等 retry_wait_s 再試；用盡拋 SimulatorOpenFailed。"""
    last = None
    for i in range(attempts):
        try:
            guarded_call(sim.open, timeout_s, sim.kill)
            return
        except Exception as e:  # noqa: BLE001
            last = e
            try:
                sim.kill()
            except Exception:  # noqa: BLE001
                pass
            if i < attempts - 1:
                sleep(retry_wait_s)
    raise SimulatorOpenFailed(f"模擬器連續 {attempts} 次開不起來：{last}")
