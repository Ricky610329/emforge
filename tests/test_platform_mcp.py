"""平台 MCP 與 HTTP 共用操作，送件 nonce 綁定完整內容。"""
import asyncio
import json
import pytest
from emforge.platform.service import Platform
from emforge.platform.mcp_server import build_server
from emforge import testing
from tests.test_client import setup, patterns

def test_mcp_submit_query_and_confirm_binding(root):
    pytest.importorskip("mcp")
    from mcp import Client
    rt = setup(root)
    srv = build_server(Platform(rt.depot))
    async def run():
        async with Client(srv) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            assert tools["platform_query"].annotations.read_only_hint
            reference = await client.call_tool("platform_reference", {})
            ref = json.loads(reference.content[0].text)
            assert "description" in ref["operations"]
            assert "run_stop" not in ref["operations"]
            assert tools["platform_reference"].annotations.read_only_hint
            resource = await client.read_resource("platform://reference")
            assert json.loads(resource.contents[0].text) == ref
            assert not {"promote", "estop_clear", "edit_code"} & tools.keys()
            args = {"profile": "fake_f1", "name": "anneal", "run_id": "run_mcp",
                    "items": [{"pattern": patterns(1)[0].astype(int).tolist()}], "request_id": "round_one"}
            first = await client.call_tool("inbox_submit", args)
            token = json.loads(first.content[0].text)["token"]
            changed = await client.call_tool("inbox_submit", {**args, "run_id": "other", "confirm": token})
            assert changed.is_error
            final = await client.call_tool("inbox_submit", {**args, "confirm": token})
            sid = json.loads(final.content[0].text)["sid"]
            again = await client.call_tool("inbox_submit", {**args, "confirm": token})
            assert again.is_error
            rt.tick()
            testing.run_all_jobs(root)
            from emforge.runtime import collect
            collect.collect(rt)
            result = await client.call_tool("platform_query", {"operation": "submission_results",
                "params": {"profile": "fake_f1", "name": "anneal", "run_id": "run_mcp", "sid": sid}})
            assert not result.is_error and len(json.loads(result.content[0].text)["result"]) == 1
            denied = await client.call_tool("platform_query", {"operation": "run_stop", "params": {}})
            assert denied.is_error
    asyncio.run(run())

def test_remote_evaluation_rescores_and_cli(root, capsys):
    from emforge import specs
    from dataclasses import replace
    from emforge.client import Client
    from emforge.cli import main
    from emforge.platform.http_server import serving
    from emforge.platform.transport import RemotePlatform
    from emforge.runtime import collect
    rt = setup(root)
    client = Client(rt.depot, "fake_f1", "anneal", run_id="run_eval")
    sid = client.submit(patterns(2), preds=[1, 2])
    for _ in range(2):
        rt.tick()
        testing.run_all_jobs(root)
        collect.collect(rt)
    assert client.status(sid)["state"] == "completed"
    old = specs.get_spec(rt.profile.spec)
    new = specs.register_spec(replace(old, name="eval_shift", offsets=tuple(v+100 for v in old.offsets)))
    with serving(Platform(rt.depot)) as url:
        remote = RemotePlatform(url)
        a = remote.call("evaluate", profile="fake_f1")
        b = remote.call("evaluate", profile="fake_f1", spec=new.name)
        assert b["result"][-1]["best"] == pytest.approx(a["result"][-1]["best"]+100)
        capsys.readouterr()
        assert main(["report", "--endpoint", url, "--profile", "fake_f1", "--calibration"]) == 0
        assert json.loads(capsys.readouterr().out)["result"]["n"] == 2
