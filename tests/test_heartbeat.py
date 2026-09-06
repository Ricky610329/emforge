# -*- coding: utf-8 -*-
"""tests/test_heartbeat.py — `emforge/heartbeat.py`：runtime 與儀器共用的背景心跳。

防什麼：review-7（一個 tick 超過 stale 門檻）、§12-16（鎖丟了仍 tick）：fn 回 False＝租約不在了 → `lost` 旗標。
"""
import threading
import time

from emforge.heartbeat import Heartbeat


def test_heartbeat_calls_fn_periodically_and_stops():
    n = []
    hb = Heartbeat(lambda: n.append(1) or True, 0.02)
    hb.start()
    time.sleep(0.2)
    hb.stop()
    k = len(n)
    assert k >= 3 and not hb.is_alive() and not hb.lost.is_set()
    time.sleep(0.05)
    assert len(n) == k, "stop 之後不再呼叫"


def test_heartbeat_sets_lost_when_fn_returns_false():
    calls = []

    def fn():
        calls.append(1)
        return len(calls) < 3

    hb = Heartbeat(fn, 0.01)
    hb.start()
    assert hb.lost.wait(2.0)
    hb.stop()
    assert len(calls) >= 3


def test_heartbeat_swallows_exceptions_and_keeps_running():
    calls = []

    def fn():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("NAS 斷線")
        return True

    hb = Heartbeat(fn, 0.01)
    hb.start()
    time.sleep(0.1)
    hb.stop()
    assert len(calls) >= 2 and not hb.lost.is_set(), "例外≠丟失（不知道），不立 lost"
    assert isinstance(hb, threading.Thread) and hb.daemon
