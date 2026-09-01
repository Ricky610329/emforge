# -*- coding: utf-8 -*-
"""tests/worker/test_fuse.py — `emforge/worker/fuse.py`：連敗保險絲純狀態機。

防什麼：I-1／2026-07-15 連環 COM 例外時 worker 一直撞牆；也防「一次失敗就判死」（COM 偶發錯 ~15%）。
"""
from emforge.worker.fuse import Fuse


def test_fuse_resets_on_success():
    f = Fuse(max_fail=3, cooldown_s=0, max_blowout=2)
    assert f.failure() == "ok" and f.failure() == "ok"
    f.success()
    assert f.fails == 0
    assert f.failure() == "ok" and f.failure() == "ok", "重新數"


def test_fuse_trips_after_max_fail_enters_cooldown():
    f = Fuse(max_fail=2, cooldown_s=600, max_blowout=3)
    assert f.failure() == "ok"
    assert f.failure() == "cooldown"
    assert f.fails == 0 and f.blowouts == 1 and not f.blown
    assert f.cooldown_s == 600


def test_fuse_blows_out_after_max_blowout():
    f = Fuse(max_fail=1, cooldown_s=0, max_blowout=2)
    assert f.failure() == "cooldown"
    assert f.failure() == "blown"
    assert f.blown and f.blowouts == 2
    assert f.failure() == "blown", "熔斷後永遠 blown"


def test_fuse_defaults_match_old_line():
    f = Fuse()
    assert (f.max_fail, f.cooldown_s, f.max_blowout) == (5, 600.0, 3)
