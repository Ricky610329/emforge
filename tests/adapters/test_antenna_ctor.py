# -*- coding: utf-8 -*-
"""tests/adapters/test_antenna_ctor.py — 需要舊 repo＋pywin32 才跑；**不開 HFSS**（DispatchEx 埋炸彈）。

跨實作對帳：profile.kwargs 每個鍵都必須是舊建構子的參數（否則發車當下才 TypeError）；
顯式寫進 profile 的求解預設值要等於舊建構子的預設（防抄錯）。
"""
import inspect

import pytest

from emforge import profiles
from emforge.adapters.antenna import _bind, sim
from emforge.adapters.antenna import profiles as aprof

sims = None
try:
    pytest.importorskip("win32com")
    sims = _bind.load_sims()
except _bind.AntennaUnavailable:
    pass

pytestmark = pytest.mark.skipif(sims is None, reason="antenna.patch 不可 import（需 EMFORGE_ANTENNA_REPO＋pywin32）")


def _init_params(cls) -> dict:
    """沿 MRO 找第一個沒有 **kwargs 的 __init__（single_rad 純轉發）。"""
    for c in cls.__mro__:
        init = c.__dict__.get("__init__")
        if init is None:
            continue
        params = inspect.signature(init).parameters
        if not any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
            return params
    raise AssertionError("找不到具名參數的 __init__")


@pytest.fixture(autouse=True)
def _reg():
    aprof.register_all()


def test_kwargs_reconcile_with_constructor_signature_via_mro():
    for name, old in (("dual_p01_db075", sims.dual_port.DualPortSimulator),
                      ("single_db100", sims.single_port_rad.SinglePortRadSimulator)):
        p = profiles.get_profile(name)
        params = _init_params(old)
        for k in p.kwargs:
            assert k in params, f"{name}: {k} 不是 {old.__name__} 的建構參數"
        assert not (set(p.kwargs) & sim.FORBIDDEN_KWARGS)


def test_explicit_defaults_equal_old_constructor_defaults():
    p = profiles.get_profile("dual_p01_db075")
    params = _init_params(sims.dual_port.DualPortSimulator)
    for k in ("max_delta_s", "max_passes", "min_passes", "min_converged", "pixel_count", "sweep_type"):
        assert p.kwargs[k] == params[k].default, k


def test_construct_creates_workdir_not_com(tmp_path, monkeypatch):
    import win32com.client

    def bomb(*a, **k):
        raise AssertionError("建構階段不准碰 COM")

    monkeypatch.setattr(win32com.client, "DispatchEx", bomb)
    s = sim.DualPortSim(workdir=str(tmp_path), profile=profiles.get_profile("dual_p01_db075"))
    assert (tmp_path / "HFSS" / "project").is_dir()
    assert s.geom_ver == "p01"
