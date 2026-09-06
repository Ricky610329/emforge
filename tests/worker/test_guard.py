# -*- coding: utf-8 -*-
"""tests/worker/test_guard.py — `emforge/worker/guard.py`：看門狗、處決線、開模擬器重試。

防什麼：COM 呼叫可以「無例外地永遠不回來」（2026-07-10）；開啟／重開卡死（218，2026-07-11）。
"""
import threading
import time

import pytest

from emforge.worker import guard


def test_watchdog_calls_kill_after_timeout_not_before():
    kills = []
    with guard.Watchdog(0.5, lambda: kills.append(1)) as wd:
        time.sleep(0.05)
    assert not wd.fired and kills == []
    with guard.Watchdog(0.1, lambda: kills.append(1)) as wd:
        time.sleep(0.4)
    assert wd.fired and kills == [1]


def test_guarded_call_returns_value_or_raises_watchdog_timeout():
    assert guard.guarded_call(lambda: 42, 1.0, lambda: None) == 42
    stop = threading.Event()

    def hang():
        stop.wait()
        raise RuntimeError("killed")

    t0 = time.time()
    with pytest.raises(guard.WatchdogTimeout, match="killed"):
        guard.guarded_call(hang, 0.2, stop.set)
    assert time.time() - t0 < 5


def test_guarded_call_reraises_plain_errors_when_not_fired():
    def boom():
        raise ValueError("plain")

    with pytest.raises(ValueError, match="plain"):
        guard.guarded_call(boom, 1.0, lambda: None)


class _Sim:
    def __init__(self, fail_first=0):
        self.fail_first, self.opens, self.kills = fail_first, 0, 0

    def open(self):
        self.opens += 1
        if self.opens <= self.fail_first:
            raise RuntimeError(f"open failed #{self.opens}")

    def kill(self):
        self.kills += 1


def test_open_with_retries_gives_up_after_3_and_kills_between():
    slept = []
    sim = _Sim(fail_first=99)
    with pytest.raises(guard.SimulatorOpenFailed):
        guard.open_with_retries(sim, attempts=3, timeout_s=5, sleep=slept.append, retry_wait_s=15)
    assert sim.opens == 3 and sim.kills == 3 and slept == [15, 15]
    ok = _Sim(fail_first=1)
    guard.open_with_retries(ok, attempts=3, timeout_s=5, sleep=slept.append, retry_wait_s=1)
    assert ok.opens == 2 and ok.kills == 1


def test_open_with_retries_reraises_fatal_types_immediately_without_retry():
    """M15：急停／前置檢查不過不是「機器卡住」，重試三次沒意義——列進 fatal 的例外原樣、立刻拋。"""
    class Refused(Exception):
        pass

    class _Refusing(_Sim):
        def open(self):
            self.opens += 1
            raise Refused("急停")

    sim, slept = _Refusing(), []
    with pytest.raises(Refused):
        guard.open_with_retries(sim, attempts=3, timeout_s=5, sleep=slept.append, retry_wait_s=15, fatal=(Refused,))
    assert sim.opens == 1 and sim.kills == 0 and slept == []
    with pytest.raises(guard.SimulatorOpenFailed):
        guard.open_with_retries(_Refusing(), attempts=2, timeout_s=5, sleep=slept.append, retry_wait_s=1)


def test_guarded_call_aborts_when_predicate_becomes_true():
    """M13：e-stop 進來時正在跑的那筆要被殺——`abort_if` 每 poll_s 查一次，真了就 on_timeout（kill）→ 拋 Aborted。"""
    flag, kills, estop = threading.Event(), [], []

    def hang():
        flag.wait(5)
        raise RuntimeError("killed")

    def kill():
        kills.append(1)
        flag.set()

    threading.Timer(0.1, lambda: estop.append(1)).start()
    t0 = time.time()
    with pytest.raises(guard.Aborted):
        guard.guarded_call(hang, 10.0, kill, abort_if=lambda: bool(estop), poll_s=0.02)
    assert time.time() - t0 < 3 and kills == [1]
    assert issubclass(guard.Aborted, guard.WatchdogTimeout), "呼叫端的 except WatchdogTimeout 仍接得到"
    assert guard.guarded_call(lambda: 7, 1.0, kill, abort_if=lambda: False, poll_s=0.01) == 7
