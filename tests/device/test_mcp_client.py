# -*- coding: utf-8 -*-
"""tests/device/test_mcp_client.py — `emforge/device/mcp_client.py`：對一台儀器的 MCP server 下單（`device-simulate` 的本體）。

走真的 streamable-HTTP 協定，但經 `httpx2.ASGITransport` 打進 `mcp_server.http_app`——不開 socket；bearer 也真的驗。
"""
import asyncio

import pytest

pytest.importorskip("mcp")

from emforge.device import mcp_client as C, mcp_server as M  # noqa: E402
from tests.device.conftest import P, make_inst, some_bits  # noqa: E402

BASE = "http://192.168.1.216:8765"


def _bits_str(arr) -> str:
    return "".join("1" if b else "0" for b in arr.reshape(-1))


def test_call_device_async_over_asgi_two_step_simulate_and_error_surface(root):
    inst = make_inst(root)
    inst.start()
    app = M.http_app(inst, secret="s3cret", host="0.0.0.0")
    bits = _bits_str(some_bits(11))

    async def go():
        import httpx2

        def hc(token="s3cret"):
            return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE, headers=C.bearer_headers(token))

        async with M.app_lifespan(app):
            st = await C.call_device_async(f"{BASE}/mcp", "device_state", {}, http_client=hc())
            assert st["tag"] == "216"
            r1 = await C.call_device_async(f"{BASE}/mcp", "device_simulate", {"profile": P.name, "bits": bits, "by": "cli"},
                                           http_client=hc())
            assert r1["needs_confirm"] is True
            r2 = await C.call_device_async(f"{BASE}/mcp", "device_simulate",
                                           {"profile": P.name, "bits": bits, "by": "cli", "confirm": r1["token"]}, http_client=hc())
            assert r2["status"] == "done" and r2["id"] == r1["record_id"]
            with pytest.raises(C.DeviceCallFailed, match="bad_bits"):
                await C.call_device_async(f"{BASE}/mcp", "device_simulate", {"profile": P.name, "bits": "01"}, http_client=hc())
            with pytest.raises(Exception):  # noqa: B017 — 401 在協定層炸出來（型別由 SDK 決定），重點是不會靜默成功
                await C.call_device_async(f"{BASE}/mcp", "device_state", {}, http_client=hc(token="wrong"))

    try:
        asyncio.run(go())
    finally:
        inst.stop()


def test_call_device_sync_wrapper_builds_bearer_client_and_runs(monkeypatch):
    seen = {}

    async def fake_async(url, tool, args, *, http_client):
        seen.update(url=url, tool=tool, args=args, auth=dict(http_client.headers).get("authorization"),
                    timeout=http_client.timeout)
        return {"ok": True}

    monkeypatch.setattr(C, "call_device_async", fake_async)
    assert C.call_device("http://127.0.0.1:8765/mcp", "device_state", {}, token="s3cret", timeout_s=42) == {"ok": True}
    assert seen["auth"] == "Bearer s3cret" and seen["tool"] == "device_state" and seen["timeout"].read == 42
    assert C.bearer_headers(None) == {}
