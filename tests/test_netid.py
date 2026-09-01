# -*- coding: utf-8 -*-
"""tests/test_netid.py — `emforge/netid.py`：機器 tag（IP 末段）。"""
from emforge import netid


def test_local_tag_prefers_env_then_last_octet(monkeypatch):
    monkeypatch.setenv("EMFORGE_MACHINE", "lab7")
    assert netid.local_tag() == "lab7"
    monkeypatch.delenv("EMFORGE_MACHINE")
    monkeypatch.setattr(netid, "local_ip", lambda: "140.213.106.216")
    assert netid.local_tag() == "216"


def test_local_ip_returns_dotted_quad_or_loopback():
    ip = netid.local_ip()
    parts = ip.split(".")
    assert len(parts) == 4 and all(p.isdigit() for p in parts)
