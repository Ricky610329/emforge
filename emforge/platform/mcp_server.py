"""平台 MCP 薄介面；optional SDK 只在函式內載入。"""
import json
from .confirmation import Confirmation

READ_OPERATIONS = {"description", "submission_status", "submission_results", "db_query", "db_top",
                   "db_sample", "db_lineage", "db_children", "db_runs", "db_profiles", "inbox_list",
                   "run_status", "run_list", "run_logs", "evaluate", "report", "run_usage", "platform_state"}
INSTRUCTIONS = ("平台協調算法與模擬節點。platform_query 的 operation 使用 resource 所列唯讀操作。"
                "inbox_submit 第一次拿預覽與 token，再以相同內容及 confirm 送件。"
                "algorithm_start 只執行已註冊版本、指定節點及現有 Python 環境。"
                "先呼叫 platform_reference 取得操作簽名，再查 description/db_profiles。"
                "submission_status/submission_results 的 params 身分為 profile/name/run_id/sid。"
                "items 各含二維 0/1 pattern，須符合 description 的 shape/fixed_on；"
                "可帶 parent、tag、priority（urgent/normal/background）。"
                "算法版本從既有 run identity 或 CLI 註冊結果取得。"
                "送件確認 token 綁定內容；這不是人工批准或身分授權。"
                "沒有改碼、promote 或解除急停工具。")

def build_server(platform):
    from mcp.server import MCPServer
    srv = MCPServer("emforge-platform", instructions=INSTRUCTIONS)
    _reads(srv, platform)
    _writes(srv, platform, Confirmation())
    return srv

def _reads(srv, platform):
    from mcp.types import ToolAnnotations
    @srv.tool(annotations=ToolAnnotations(read_only_hint=True))
    def platform_query(operation: str, params: dict | None = None) -> dict:
        """唯讀查詢；操作與欄位語意見 platform://reference。"""
        if operation not in READ_OPERATIONS:
            raise ValueError("未知或非唯讀操作")
        return {"result": platform.call(operation, **(params or {}))}

    @srv.resource("platform://reference", mime_type="application/json")
    def reference() -> str:
        return json.dumps(platform_reference(), ensure_ascii=False)

    @srv.tool(annotations=ToolAnnotations(read_only_hint=True))
    def platform_reference() -> dict:
        """Discover query signatures and workflow before using emforge tools."""
        import inspect
        from .service import Platform
        return {"instructions": INSTRUCTIONS,
                "operations": {op: str(inspect.signature(getattr(Platform, op)))
                               for op in sorted(READ_OPERATIONS)}}

    @srv.resource("platform://state", mime_type="application/json")
    def state() -> str:
        return json.dumps(platform.call("platform_state"), ensure_ascii=False)

def _writes(srv, platform, confirmations):
    @srv.tool()
    def inbox_submit(profile: str, name: str, run_id: str, items: list[dict],
                     request_id: str, spec: str | None = None, confirm: str | None = None) -> dict:
        """兩段式送件；request_id 在 run 內固定，傳輸中斷可安全重新握手。"""
        payload = dict(profile=profile, name=name, run_id=run_id, items=items, request_id=request_id, spec=spec)
        return confirmations.execute("submit", payload, confirm,
                                     lambda: {"sid": platform.call("submit", **payload)})

    @srv.tool()
    def algorithm_start(name: str, version: str, run_id: str, node: str, environment: str,
                        profile: str, budget: int, params: dict | None = None, seed: int = 0,
                        spec: str | None = None, resume_from: str | None = None) -> dict:
        """啟動已登錄版本；指定機器，不安裝環境。"""
        return platform.call("run_start", name=name, version=version, run_id=run_id, node=node,
                             environment=environment, profile=profile, budget=budget, params=params,
                             seed=seed, spec=spec, resume_from=resume_from)

    @srv.tool()
    def algorithm_stop(run_id: str) -> dict:
        """要求單一算法停止；保留已送量測與 checkpoint。"""
        return platform.call("run_stop", run_id=run_id)

def serve(platform, host="127.0.0.1", port=8767, secret=None):
    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings
    from ..device.mcp_server import BearerGate, is_loopback
    if not is_loopback(host) and not secret:
        raise ValueError("非 loopback 需要 EMFORGE_PLATFORM_TOKEN")
    ts = None if is_loopback(host) else TransportSecuritySettings(enable_dns_rebinding_protection=False)
    app = build_server(platform).streamable_http_app(streamable_http_path="/mcp", json_response=True,
                 stateless_http=True, transport_security=ts, host=host)
    if secret:
        app = BearerGate(app, secret)
    uvicorn.run(app, host=host, port=port, log_level="warning")


def serve_stdio(platform):
    """One agent session owns one MCP server and its confirmation nonces."""
    build_server(platform).run(transport="stdio")
