"""emforge/device/mcp_client.py — 對一台儀器的 MCP server 下單（M16）：`emforge device-simulate` 的本體，測試也用。

`mcp`／`httpx2`／`anyio` 只在函式內 import（optional extra `emforge[mcp]`）。tool 回 is_error → `DeviceCallFailed("<code>: …")`
（原文就是 server 端的錯誤碼，agent／人照碼分支）。模擬一筆 100–250 s：讀逾時要拉長（`timeout_s`）。
"""
import json


class DeviceCallFailed(Exception):
    """tool 回 is_error：訊息＝server 端 ToolError 原文（"<code>: …"）。"""


def bearer_headers(token: str | None) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


async def call_device_async(url: str, tool: str, args: dict, *, http_client=None) -> dict:
    """一個 tool 呼叫：連 → call → 關。`http_client` 可注入（測試走 ASGITransport；正式由 `call_device` 建帶 bearer 的）。"""
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client
    async with Client(streamable_http_client(url, http_client=http_client)) as c:
        r = await c.call_tool(tool, args)
    text = r.content[0].text if r.content else ""
    if r.is_error:
        raise DeviceCallFailed(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}


def call_device(url: str, tool: str, args: dict, *, token: str | None = None, timeout_s: float = 3600.0) -> dict:
    """同步版：自建 httpx2 client（bearer＋長讀逾時），跑在自己的事件迴圈。"""
    import asyncio
    import httpx2

    async def go():
        async with httpx2.AsyncClient(headers=bearer_headers(token), timeout=httpx2.Timeout(float(timeout_s))) as hc:
            return await call_device_async(url, tool, args, http_client=hc)

    return asyncio.run(go())
