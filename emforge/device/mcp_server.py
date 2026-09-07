"""emforge/device/mcp_server.py — 每台儀器自己的 MCP server（M15）：把 `Instrument` 的程序一對一貼成 tools。

**唯一** import `mcp`／`uvicorn`／`starlette` 的地方，而且都在函式內（optional extra `emforge[mcp]`；核心 import 期零 mcp，
`tests/test_smoke.py` 釘死）——沒裝 mcp 的機器照常跑 worker／runtime／CLI。

tools（同步 def → SDK 丟 thread pool，所以 simulate 阻塞時 device_state 照回）：
  read-only  device_state／device_describe／device_selfcheck／device_log
  mutating   device_simulate（兩段式）／device_abort／device_estop（**只有 engage**；解除只能 CLI）／
             device_stop_worker／device_resume_worker（兩段式，＝建／刪 queue/STOP.<tag>）
resources：device://state（json）／device://reference（markdown）／device://reference.json。
錯誤全轉 `ToolError("<code>: …")`：device_busy／estop_engaged／precondition_failed／confirm_rejected／bad_bits／unknown_profile／
open_failed／internal；模擬本身失敗是資料（`status=error`），不是 ToolError。

auth：共享 `EMFORGE_DEVICE_TOKEN`（bearer，`hmac.compare_digest`）。#? 選 ASGI 中介層而不是 SDK 的 `token_verifier`：SDK 那條是
OAuth 資源伺服器（`AuthSettings` 要 issuer_url／resource_server_url、掛 /.well-known 端點），共享密鑰不是 OAuth；中介層 15 行、
可用 `httpx2.ASGITransport` 免 socket 測。**非 loopback 綁定且無 token → 拒起**。
與 worker 同一行程（daemon thread；同機只能一個 HFSS 使用者）；`device-serve` 是不撿佇列的獨立版。
"""
import functools
import hmac
import json
import socket
import threading
import time

import numpy as np

from .. import netid, paths, profiles
from ..queue import Queue
from ..worker.guard import SimulatorOpenFailed
from . import estop, reference
from .instrument import ConfirmRejected, DeviceBusy, PreconditionFailed

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
UNSPECIFIED_HOSTS = ("0.0.0.0", "", "::")
DEFAULT_HOST, DEFAULT_PORT, MCP_PATH = "127.0.0.1", 8765, "/mcp"
#? 例外 → 錯誤碼（agent 照碼分支，不解析中文訊息）。
ERROR_CODES = ((DeviceBusy, "device_busy"), (estop.EstopEngaged, "estop_engaged"),
               (PreconditionFailed, "precondition_failed"), (ConfirmRejected, "confirm_rejected"),
               (SimulatorOpenFailed, "open_failed"), (profiles.UnknownProfile, "unknown_profile"))


# ── 綁定／URL ───────────────────────────────────────────────────────────────
def is_loopback(host: str) -> bool:
    return host in LOOPBACK_HOSTS


def check_bind(host: str, secret: str | None) -> None:
    """非 loopback 又沒 token＝整個區網都能開 HFSS → 拒起。"""
    if not is_loopback(host) and not secret:
        raise ValueError(f"綁 {host} 需要 EMFORGE_DEVICE_TOKEN（無 token 只准 127.0.0.1）")


def endpoint_url(host: str, port: int) -> str:
    """寫進狀態字典／`fleet --mcp-config` 的位址：綁 0.0.0.0 時給對外介面 IP（別台才連得到）。"""
    shown = netid.local_ip() if host in UNSPECIFIED_HOSTS else host
    return f"http://{shown}:{port}{MCP_PATH}"


# ── 錯誤與輸入 ──────────────────────────────────────────────────────────────
def tool_error(e: Exception):
    """例外 → `ToolError("<code>: …")`；agent 看得到原因。"""
    from mcp.server.mcpserver.exceptions import ToolError
    for cls, code in ERROR_CODES:
        if isinstance(e, cls):
            return ToolError(f"{code}: {e}")
    if isinstance(e, ValueError) and str(e).startswith("bad_bits"):
        return ToolError(str(e))
    return ToolError(f"internal: {type(e).__name__}: {e}")


def _guarded(fn):
    """tool 本體的例外 → ToolError（`wraps` 保住簽名與型別註解，SDK 才推得出 schema）。"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 — tool 邊界：任何例外都要變成 agent 看得懂的錯誤碼
            raise tool_error(e) from e
    return wrapper


def parse_bits(profile, bits) -> np.ndarray:
    """'0101…'（可含空白／換行）或 list（平坦或巢狀）→ bool 陣列（shape＝profile.shape）；錯 → ValueError("bad_bits…")。"""
    if isinstance(bits, str):
        s = "".join(bits.split())
        if set(s) - {"0", "1"}:
            raise ValueError("bad_bits: 字串只能含 0／1")
        arr = np.array([c == "1" for c in s], dtype=bool)
    else:
        try:
            arr = np.asarray(bits)
        except ValueError as e:
            raise ValueError(f"bad_bits: 不是規則的 list（{e}）") from None
        if arr.dtype != bool:
            arr = arr > 0.5
    n = int(np.prod(profile.shape))
    if arr.ndim == 1 and arr.size == n:
        arr = arr.reshape(profile.shape)
    if arr.shape != tuple(profile.shape):
        raise ValueError(f"bad_bits: 需要 {tuple(profile.shape)}（{n} 個 0／1），拿到 shape {arr.shape}")
    return arr


# ── server ──────────────────────────────────────────────────────────────────
def build_server(inst):
    """tools＋resources 貼在這台 `Instrument` 上；回 `MCPServer`（測試用 `Client(server)` in-memory 打）。"""
    from mcp.server import MCPServer
    srv = MCPServer(f"emforge-{inst.tag}", instructions=_instructions(inst))
    _add_read_tools(srv, inst)
    _add_simulate_tools(srv, inst)
    _add_worker_tools(srv, inst)
    _add_resources(srv, inst)
    return srv


def _instructions(inst) -> str:
    return (f"emforge 儀器 {inst.tag}（HFSS 模擬器）。先 device_describe 看能量哪些 profile 與限制；"
            "device_simulate 兩段式：不帶 confirm 先拿 token 與預覽，再帶 confirm 跑一筆（結果不入資料庫）。"
            "急停只能按不能解（解除走 CLI：emforge device-estop clear）。")


def _add_read_tools(srv, inst) -> None:
    from mcp.types import ToolAnnotations
    ro = ToolAnnotations(read_only_hint=True)

    @srv.tool(annotations=ro)
    @_guarded
    def device_state() -> dict:
        """狀態字典（state／owner／store／profile／n_done／n_error／estop／url…）＋最近 10 個狀態轉換。offline 由讀者推導。"""
        return {**inst.state.to_dict(), "history": [list(h) for h in inst.history[-10:]]}

    @srv.tool(annotations=ro)
    @_guarded
    def device_describe() -> str:
        """說明檔（markdown）：能量的 profile、限制、體檢、近期一筆多久、程序清單。"""
        return reference.render(inst)[0]

    @srv.tool(annotations=ro)
    @_guarded
    def device_selfcheck() -> dict:
        """depot 探針＋機器體檢＋狀態；problems 空＝可開。"""
        return inst.selfcheck()

    @srv.tool(annotations=ro)
    @_guarded
    def device_log(n: int = 50) -> dict:
        """裝置日誌最後 n 筆（device_*／lease_refused／estop_*）：{n, entries}。"""
        entries = inst.depot.read_log(paths.device_log(inst.tag))[-max(1, int(n)):]
        return {"n": len(entries), "entries": entries}


def _add_simulate_tools(srv, inst) -> None:
    @srv.tool()
    @_guarded
    def device_simulate(profile: str, bits: str | list, confirm: str | None = None, by: str = "mcp") -> dict:
        """跑一筆（兩段式）。bits＝'0101…'（H×W 個 0／1，可含換行）或 list。第一次不帶 confirm →
        {needs_confirm, token, record_id, preview}；帶 confirm 再呼叫 → 結果 dict（status done／error；error 是資料不是錯誤）。
        結果不入資料庫（要 provenance 用 CLI smoke）。忙碌／急停會拒，token 不燒。"""
        p = profiles.get_profile(profile)
        return inst.simulate_once(p.name, parse_bits(p, bits), by=by, confirm=confirm)

    @srv.tool()
    @_guarded
    def device_abort(by: str = "mcp") -> dict:
        """殺正在跑的那筆（＝kill；那筆算 error、吃一次 attempts）。不需要租約。"""
        inst.abort(by=f"mcp:{by}")
        return {"aborted": True, "state": inst.state.state, "current_id": inst.state.current_id}

    @srv.tool()
    @_guarded
    def device_estop(reason: str, by: str = "mcp") -> dict:
        """按下這台的急停（單機層 queue/ESTOP.<tag>）：open／simulate 硬擋、正在跑的那筆 abort、worker job 之間不撿。
        解除只能 CLI：emforge device-estop clear --tag <tag> --confirm。"""
        key = estop.engage(inst.depot, inst.tag, by=f"mcp:{by}", reason=reason)
        return {"engaged": True, "key": key, "estop": inst.estop_engaged()}


def _add_worker_tools(srv, inst) -> None:
    def two_step(op: str, effect: str, action, confirm, by) -> dict:
        key = paths.queue_stop(inst.tag)
        if confirm is None:
            return {"needs_confirm": True, "token": inst.issue_confirm(op, key),
                    "preview": {"op": op, "key": key, "tag": inst.tag, "effect": effect}}
        if not inst.check_confirm(op, key, confirm):
            raise ConfirmRejected(f"token {confirm!r} 不對、過期或用過（先不帶 confirm 拿新 token）")
        action()
        inst.consume_confirm(confirm)
        inst.log(f"device_{op}", by=f"mcp:{by}")
        return {"done": True, "op": op, "key": key, "stop_requested": Queue(inst.depot).stop_requested(inst.tag)}

    @srv.tool()
    @_guarded
    def device_stop_worker(confirm: str | None = None, by: str = "mcp") -> dict:
        """建 queue/STOP.<tag>：這台 worker 跑完當前 job 收工（兩段式：先拿 token 再帶 confirm）。"""
        return two_step("stop_worker", "worker 跑完當前 job 收工", lambda: Queue(inst.depot).request_stop(inst.tag),
                        confirm, by)

    @srv.tool()
    @_guarded
    def device_resume_worker(confirm: str | None = None, by: str = "mcp") -> dict:
        """刪 queue/STOP.<tag>（兩段式）。worker 行程若已收工要人重啟（scripts/start_worker.cmd）。"""
        return two_step("resume_worker", "刪 STOP 檔；worker 若已退出要人重啟",
                        lambda: Queue(inst.depot).clear_stop(inst.tag), confirm, by)


def _add_resources(srv, inst) -> None:
    @srv.resource("device://state", mime_type="application/json")
    def state_resource() -> str:
        return json.dumps(inst.state.to_dict(), ensure_ascii=False, indent=1)

    @srv.resource("device://reference", mime_type="text/markdown")
    def reference_resource() -> str:
        return reference.render(inst)[0]

    @srv.resource("device://reference.json", mime_type="application/json")
    def reference_json_resource() -> str:
        return json.dumps(reference.render(inst)[1], ensure_ascii=False, indent=1)


# ── HTTP ────────────────────────────────────────────────────────────────────
class BearerGate:
    """ASGI 中介層：http 請求沒帶 `Authorization: Bearer <token>` → 401；lifespan 等其他 scope 直通（內層在 `.app`）。"""

    def __init__(self, app, secret: str):
        self.app, self._expected = app, f"Bearer {secret}".encode("utf-8")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            got = dict(scope.get("headers") or ()).get(b"authorization", b"")
            if not hmac.compare_digest(got, self._expected):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"www-authenticate", b"Bearer"), (b"content-type", b"text/plain; charset=utf-8")]})
                await send({"type": "http.response.body",
                            "body": "unauthorized：要帶 Authorization: Bearer <EMFORGE_DEVICE_TOKEN>".encode("utf-8")})
                return
        await self.app(scope, receive, send)


def http_app(inst, *, secret: str | None, host: str = DEFAULT_HOST, json_response: bool = True, stateless: bool = True):
    """ASGI app：loopback 沿用 SDK 的 DNS-rebinding 防護（Host 白名單）；非 loopback 靠 bearer——沒 token 拒起。"""
    check_bind(host, secret)
    from mcp.server.transport_security import TransportSecuritySettings
    ts = None if is_loopback(host) else TransportSecuritySettings(enable_dns_rebinding_protection=False)
    app = build_server(inst).streamable_http_app(streamable_http_path=MCP_PATH, json_response=json_response,
                                                 stateless_http=stateless, transport_security=ts, host=host)
    return BearerGate(app, secret) if secret else app


def app_lifespan(app):
    """Starlette 的 lifespan（session manager 要在裡面跑）；uvicorn 自己會進，免 socket 測試要手動進。"""
    inner = getattr(app, "app", app)
    return inner.router.lifespan_context(inner)


def _precheck_port(host: str, port: int) -> None:
    """先用一個不帶 SO_REUSEADDR 的 socket 綁一次再放掉：埠被佔立刻拋，訊息指名埠（uvicorn 的失敗是 sys.exit(3)＋一行 log）。"""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError as e:
            raise RuntimeError(f"MCP 埠 {host}:{port} 綁不上（被佔？前一個 worker／device-serve 沒退乾淨？）：{e}") from e


def serve(inst, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, secret: str | None = None,
          json_response: bool = True, stateless: bool = True, ready: threading.Event | None = None,
          hold: dict | None = None) -> None:
    """阻塞跑到行程結束（uvicorn；非主執行緒也能跑——uvicorn 只在主執行緒掛訊號）。
    #! 檢查 #13（2026-09-07）：以前起 server 之前就 announce_url＋記 device_serve——埠被佔時 uvicorn 在 daemon thread 裡 sys.exit(3)
    #  被靜默吞掉，狀態字典卻宣稱 server 起來了、fleet --mcp-config 吐一個連不上的端點。現在**綁定成功（server.started）才宣告**；
    #  綁不上／沒起來一律 RuntimeError（SystemExit 也轉），`ready` 給 serve_in_thread 等、`hold["server"]` 給呼叫端收。"""
    import asyncio
    import uvicorn
    app = http_app(inst, secret=secret, host=host, json_response=json_response, stateless=stateless)
    _precheck_port(host, port)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    if hold is not None:
        hold["server"] = server
    url = endpoint_url(host, port)

    async def main():
        task = asyncio.ensure_future(server.serve())
        while not server.started and not task.done():
            await asyncio.sleep(0.02)
        if not server.started:
            await task                                  # 讓它拋（SystemExit／例外）；沒拋就是靜默退出
            raise RuntimeError(f"MCP server 沒起來（{host}:{port}）")
        inst.announce_url(url)
        inst.log("device_serve", url=url, host=host, port=port, auth=bool(secret))
        if ready is not None:
            ready.set()
        await task

    try:
        asyncio.run(main())
    except SystemExit as e:                             # uvicorn 綁不上／起不來：sys.exit(3)
        raise RuntimeError(f"MCP server 沒起來（{host}:{port}；uvicorn exit {e.code}）") from None


def serve_in_thread(inst, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, secret: str | None = None,
                    timeout_s: float = 3.0) -> tuple:
    """與 worker 同一行程：MCP 在 daemon thread（同機只能一個 HFSS 使用者）。等到 server 真的綁上才回 (thread, url)；
    執行緒死了或 timeout_s 內沒 ready → RuntimeError（呼叫端據此不起 worker）。`thread.emforge_stop()` 可收掉。"""
    check_bind(host, secret)
    ready, hold, failure = threading.Event(), {}, {}

    def run():
        try:
            serve(inst, host=host, port=port, secret=secret, ready=ready, hold=hold)
        except BaseException as e:  # noqa: BLE001 — SystemExit 也要接，否則 threading.excepthook 靜默吞掉
            failure["error"] = e

    t = threading.Thread(target=run, daemon=True, name=f"emforge-mcp-{inst.tag}")
    t.start()
    deadline = time.monotonic() + timeout_s
    while not ready.is_set() and t.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    if not ready.is_set():
        why = failure.get("error") or ("執行緒已結束" if not t.is_alive() else f"{timeout_s:.0f}s 內沒綁上")
        raise RuntimeError(f"MCP server 沒起來（{host}:{port}）：{why}")
    t.emforge_stop = lambda: setattr(hold["server"], "should_exit", True) if "server" in hold else None
    return t, endpoint_url(host, port)
