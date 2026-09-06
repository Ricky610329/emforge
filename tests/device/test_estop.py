# -*- coding: utf-8 -*-
"""tests/device/test_estop.py — `emforge/device/estop.py`：MHS 第 5 層急停，三層（全機／單機／本機）。

防什麼：急停只擋一層（NAS 斷線時本機層還要擋得住）；解除路徑不只 CLI。
"""
import pytest

from emforge import paths
from emforge.depot import MemoryDepot
from emforge.device import estop


def test_engage_fleet_device_local_and_engaged_reports_outermost_scope(root):
    d = MemoryDepot()
    assert estop.engaged(d, root, "216") is None
    key = estop.engage(d, "216", by="ricky", reason="冒煙")
    assert key == paths.estop_device("216")
    e = estop.engaged(d, root, "216")
    assert e["scope"] == "device" and e["by"] == "ricky" and e["reason"] == "冒煙" and len(e["at"]) == 19
    assert estop.engaged(d, root, "218") is None, "單機急停不影響別台"
    estop.engage(d, None, by="ricky", reason="全停")
    assert estop.engaged(d, root, "216")["scope"] == "fleet", "全機層優先回報"
    assert estop.engaged(d, root, "218")["scope"] == "fleet"
    assert estop.clear(d, None) is True and estop.engaged(d, root, "216")["scope"] == "device"
    assert estop.clear(d, "216") is True and estop.engaged(d, root, "216") is None
    estop.engage_local(root, by="watchdog", reason="磁碟")
    assert estop.engaged(d, root, "216")["scope"] == "local" and paths.estop_local(root).exists()
    assert estop.clear_local(root) is True and estop.engaged(d, root, "216") is None


def test_clear_returns_false_when_nothing_engaged(root):
    d = MemoryDepot()
    assert estop.clear(d, None) is False and estop.clear(d, "216") is False and estop.clear_local(root) is False


def test_check_raises_estop_engaged_carrying_scope(root):
    d = MemoryDepot()
    estop.check(d, root, "216")
    estop.engage(d, "216", by="x", reason="r")
    with pytest.raises(estop.EstopEngaged) as ei:
        estop.check(d, root, "216")
    assert ei.value.scope == "device" and "r" in str(ei.value)
