# -*- coding: utf-8 -*-
"""tests/device/test_mcp_server.py — `emforge/device/mcp_server.py`：每台儀器的 MCP server（M15）。

全部 in-memory：tools／resources 用 SDK 的 `Client(server)`；HTTP 層（bearer、lifespan、stateless JSON）用 `httpx2.ASGITransport`
——不開 socket。防什麼：tools 與說明檔程序清單脫鉤、錯誤碼變成 agent 看不懂的 500、token 沒擋、simulate 阻塞時整台失聯。
"""
import asyncio
import json
import time

import pytest

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402

from emforge import paths  # noqa: E402
from emforge.device import estop, mcp_server as M, reference  # noqa: E402
from emforge.queue import Queue  # noqa: E402
from tests.device.conftest import P, fake_factory, make_inst, some_bits  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _data(r):
    assert not r.is_error, r.content[0].text if r.content else r
    return json.loads(r.content[0].text)


def _err(r) -> str:
    assert r.is_error, "預期 ToolError"
    return r.content[0].text


def _bits_str(arr) -> str:
    return "".join("1" if b else "0" for b in arr.reshape(-1))


@pytest.fixture
def served(root):
    inst = make_inst(root)
    inst.start()
    yield inst, M.build_server(inst)
    inst.stop()


# ── tools 清單 ──────────────────────────────────────────────────────────────
def test_tools_match_reference_procedures_and_carry_read_only_hints(served):
    _, srv = served

    async def go():
        async with Client(srv) as c:
            return (await c.list_tools()).tools

    by_name = {t.name: t for t in _run(go())}
    assert set(by_name) == {p["name"] for p in reference.PROCEDURES}, "說明檔的程序清單＝MCP tools，一對一"
    for p in reference.PROCEDURES:
        ann = by_name[p["name"]].annotations
        assert bool(ann and ann.read_only_hint) == (not p["mutating"]), p["name"]
    assert not any("clear" in n for n in by_name), "解除急停不在 MCP：只能 CLI"
    assert "bits" in by_name["device_simulate"].input_schema["properties"]


# ── simulate 兩段式 ──────────────────────────────────────────────────────────
def test_simulate_is_two_step_token_single_use_and_result_not_in_db(served):
    inst, srv = served
    bits = _bits_str(some_bits(5))

    async def go():
        async with Client(srv) as c:
            r1 = _data(await c.call_tool("device_simulate", {"profile": P.name, "bits": bits, "by": "ricky"}))
            assert r1["needs_confirm"] is True and len(r1["token"]) == 8 and r1["preview"]["profile"] == P.name
            r2 = _data(await c.call_tool("device_simulate", {"profile": P.name, "bits": bits, "by": "ricky",
                                                            "confirm": r1["token"]}))
            assert r2["status"] == "done" and r2["id"] == r1["record_id"] and r2["adhoc"] is True and r2["by"] == "ricky"
            r3 = await c.call_tool("device_simulate", {"profile": P.name, "bits": bits, "confirm": r1["token"]})
            assert _err(r3).count("confirm_rejected"), "token 單次"
            r4 = _data(await c.call_tool("device_simulate", {"profile": P.name, "bits": some_bits(5).tolist()}))
            assert r4["needs_confirm"] is True and r4["record_id"] == r1["record_id"], "list 形式的 bits 也收"

    _run(go())
    assert len(inst.depot.list(paths.adhoc_dir(inst.tag))) == 1 and inst.state.owner is None
    assert inst.depot.list(paths.db_dir(P.name)) == [], "不入 db"


def test_busy_estop_bad_bits_unknown_profile_become_tool_error_codes(served):
    inst, srv = served
    bits = _bits_str(some_bits(6))

    async def go():
        async with Client(srv) as c:
            tok = _data(await c.call_tool("device_simulate", {"profile": P.name, "bits": bits}))["token"]
            inst.acquire("queue:s9")
            assert "device_busy" in _err(await c.call_tool("device_simulate", {"profile": P.name, "bits": bits, "confirm": tok}))
            inst.release("queue:s9")
            assert "bad_bits" in _err(await c.call_tool("device_simulate", {"profile": P.name, "bits": bits[:-3]}))
            assert "bad_bits" in _err(await c.call_tool("device_simulate", {"profile": P.name, "bits": "01x1"}))
            assert "bad_bits" in _err(await c.call_tool("device_simulate", {"profile": P.name, "bits": [[1, 0], [1]]}))
            assert "unknown_profile" in _err(await c.call_tool("device_simulate", {"profile": "nope", "bits": bits}))
            estop.engage(inst.depot, inst.tag, by="ricky", reason="冒煙")
            assert "estop_engaged" in _err(await c.call_tool("device_simulate", {"profile": P.name, "bits": bits, "confirm": tok}))
            estop.clear(inst.depot, inst.tag)
            r = _data(await c.call_tool("device_simulate", {"profile": P.name, "bits": bits, "confirm": tok}))
            assert r["status"] == "done", "被拒（busy／急停）不燒 token：同窗內同一 token 還能用"

    _run(go())


def test_device_state_answers_while_simulate_blocks(root):
    inst = make_inst(root, sim_factory=fake_factory(delay_s=0.6))
    inst.start()
    srv = M.build_server(inst)
    bits = _bits_str(some_bits(7))
    done = {}

    async def go():
        async with Client(srv) as c:
            tok = _data(await c.call_tool("device_simulate", {"profile": P.name, "bits": bits}))["token"]

            async def sim():
                r = await c.call_tool("device_simulate", {"profile": P.name, "bits": bits, "confirm": tok})
                done["sim"] = time.time()
                return _data(r)

            async def state():
                await asyncio.sleep(0.15)
                r = _data(await c.call_tool("device_state", {}))
                done["state"] = time.time()
                return r

            return await asyncio.gather(sim(), state())

    try:
        res, st = _run(go())
    finally:
        inst.stop()
    assert res["status"] == "done" and st["tag"] == "216"
    assert done["state"] < done["sim"] - 0.3, "同步 tool 跑 thread pool：state 不用等 simulate"


# ── 其他程序 ────────────────────────────────────────────────────────────────
def test_abort_estop_engage_and_worker_stop_resume_two_step(served):
    inst, srv = served

    async def go():
        async with Client(srv) as c:
            a = _data(await c.call_tool("device_abort", {"by": "ricky"}))
            assert a["aborted"] is True
            e = _data(await c.call_tool("device_estop", {"reason": "冒煙", "by": "ricky"}))
            assert e["engaged"] is True and e["estop"]["scope"] == "device" and e["estop"]["by"] == "mcp:ricky"
            assert inst.state.state == "estop"
            s1 = _data(await c.call_tool("device_stop_worker", {}))
            assert s1["needs_confirm"] is True and s1["preview"]["key"] == paths.queue_stop(inst.tag)
            assert not Queue(inst.depot).stop_requested(inst.tag)
            s2 = _data(await c.call_tool("device_stop_worker", {"confirm": s1["token"]}))
            assert s2["done"] is True and Queue(inst.depot).stop_requested(inst.tag)
            assert "confirm_rejected" in _err(await c.call_tool("device_resume_worker", {"confirm": s1["token"]})), "op 不同 token 不同"
            r1 = _data(await c.call_tool("device_resume_worker", {}))
            r2 = _data(await c.call_tool("device_resume_worker", {"confirm": r1["token"]}))
            assert r2["done"] is True and not Queue(inst.depot).stop_requested(inst.tag)
            log = _data(await c.call_tool("device_log", {"n": 3}))
            assert log["n"] == 3 and len(log["entries"]) == 3 and log["entries"][-1]["event"] == "device_resume_worker"
            sc = _data(await c.call_tool("device_selfcheck", {}))
            assert sc["state"] == "estop" and "health" in sc
            md = (await c.call_tool("device_describe", {})).content[0].text
            assert "device_simulate" in md and P.name in md

    _run(go())
    estop.clear(inst.depot, inst.tag)


def test_resources_state_reference_and_reference_json(served):
    inst, srv = served

    async def go():
        async with Client(srv) as c:
            uris = {str(r.uri) for r in (await c.list_resources()).resources}
            assert uris == {"device://state", "device://reference", "device://reference.json"}
            st = (await c.read_resource("device://state")).contents[0]
            assert st.mime_type == "application/json" and json.loads(st.text)["tag"] == "216"
            ref = (await c.read_resource("device://reference")).contents[0]
            assert ref.mime_type == "text/markdown" and "# 儀器 216" in ref.text and "device_simulate" in ref.text
            rj = json.loads((await c.read_resource("device://reference.json")).contents[0].text)
            assert [p["name"] for p in rj["procedures"]] == [p["name"] for p in reference.PROCEDURES]

    _run(go())


# ── HTTP 層 ─────────────────────────────────────────────────────────────────
def test_check_bind_refuses_non_loopback_without_token_and_endpoint_url():
    with pytest.raises(ValueError, match="EMFORGE_DEVICE_TOKEN"):
        M.check_bind("0.0.0.0", None)
    M.check_bind("0.0.0.0", "s3cret")
    M.check_bind("127.0.0.1", None)
    assert M.endpoint_url("127.0.0.1", 8765) == "http://127.0.0.1:8765/mcp"
    assert M.endpoint_url("0.0.0.0", 8765).startswith("http://") and "0.0.0.0" not in M.endpoint_url("0.0.0.0", 8765)


def test_http_app_requires_bearer_off_loopback_and_serves_mcp_over_asgi(served):
    inst, _ = served
    app = M.http_app(inst, secret="s3cret", host="0.0.0.0")
    base = "http://192.168.1.216:8765"

    async def go():
        import httpx2
        from mcp.client.streamable_http import streamable_http_client
        async with M.app_lifespan(app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=base) as c:
                assert (await c.post("/mcp", json={})).status_code == 401
                assert (await c.post("/mcp", json={}, headers={"Authorization": "Bearer nope"})).status_code == 401
                assert (await c.post("/mcp", json={}, headers={"Authorization": "Bearer s3cret "})).status_code == 401
            hc = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=base,
                                    headers={"Authorization": "Bearer s3cret"})
            async with Client(streamable_http_client(f"{base}/mcp", http_client=hc)) as cl:
                assert "device_state" in {t.name for t in (await cl.list_tools()).tools}
                assert _data(await cl.call_tool("device_state", {}))["tag"] == "216"

    _run(go())


def test_http_app_loopback_without_token_has_no_bearer_gate(served):
    inst, _ = served
    app = M.http_app(inst, secret=None, host="127.0.0.1")

    async def go():
        import httpx2
        async with M.app_lifespan(app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://127.0.0.1:8765") as c:
                assert (await c.post("/mcp", json={})).status_code != 401

    _run(go())
    with pytest.raises(ValueError):
        M.http_app(inst, secret=None, host="0.0.0.0")


def test_serve_in_thread_is_daemon_and_announces_url(served, monkeypatch):
    inst, _ = served
    seen = {}
    monkeypatch.setattr(M, "serve", lambda inst, **kw: seen.update(kw))
    t, url = M.serve_in_thread(inst, host="127.0.0.1", port=8765, secret=None)
    t.join(timeout=5)
    assert t.daemon and url == "http://127.0.0.1:8765/mcp" and inst.state.url == url
    assert seen == {"host": "127.0.0.1", "port": 8765, "secret": None}
    assert inst.depot.get_json(paths.device_state(inst.tag))["url"] == url
    with pytest.raises(ValueError):
        M.serve_in_thread(inst, host="0.0.0.0", port=8765, secret=None)
