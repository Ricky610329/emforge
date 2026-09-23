"""標準函式庫 HTTP 服務：先綁埠再宣告；關閉時回收伺服器執行緒。"""
import contextlib
import hmac
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import wire

DEPOT_OPERATIONS = {"put_bytes", "get_bytes", "delete", "exists", "list", "modified_at",
                    "set_modified_at", "append", "read_log", "rewrite_log", "claim", "owner",
                    "touch", "release", "break_if_stale", "ensure_prefixes", "selfcheck", "now"}
MAX_BODY = 64 * 1024 * 1024


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64


class Handler(BaseHTTPRequestHandler):
    timeout = 30            #? 閒置連線 30 s 收回執行緒（ThreadingHTTPServer 一連線一執行緒、沒有上限）

    def log_message(self, *args):
        """不把 token 或候選寫入 HTTP access log。"""

    def _authorized(self):
        secret = self.server.secret
        actual = self.headers.get("Authorization", "")
        #? 以 bytes 比對：str 版 compare_digest 遇到非 ASCII 會 TypeError（＝500，不是 401）
        return not secret or hmac.compare_digest(actual.encode("utf-8", "replace"), ("Bearer " + secret).encode("utf-8"))

    def _browser_request(self) -> bool:
        """平台 API 只服務 emforge 自己的客戶端：帶 Origin（瀏覽器跨站）或非 JSON 的 POST 一律拒——
        loopback 無 token 的部署，網頁用 text/plain simple request 就能寫 /depot（I-26）。"""
        ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self.send_error(415, "Content-Type must be application/json")   # 狀態列只能 latin-1
            return True
        if self.headers.get("Origin"):
            self.send_error(403, "browser origins are not served")
            return True
        return False

    def do_GET(self):
        if not self._authorized():
            self.send_error(401)
            return
        if self.path != "/health":
            self.send_error(404)
            return
        self._reply({"ok": True, "value": {"ready": True}})

    def do_POST(self):
        if not self._authorized():
            self.send_error(401)
            return
        if self.path not in ("/rpc", "/depot"):
            self.send_error(404)
            return
        if self._browser_request():
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if not 0 < n <= MAX_BODY:
                raise ValueError("請求大小不合法")
            doc = wire.loads(self.rfile.read(n))
            op, params = doc["op"], doc["params"]
            if self.path == "/depot":
                if op not in DEPOT_OPERATIONS:
                    raise ValueError("未知 Depot 操作")
                value = getattr(self.server.platform.depot, op)(**params)
            else:
                value = self.server.platform.call(op, **params)
            payload = {"ok": True, "value": value}
        except Exception as e:
            payload = {"ok": False, "error": {"type": type(e).__name__, "message": str(e)}}
        self._reply(payload)

    def _reply(self, value):
        try:
            raw = wire.dumps(value)
        except (TypeError, ValueError) as e:                 # 回傳值序列化失敗也要回 ok:false，不能斷線（I-25）
            raw = wire.dumps({"ok": False, "error": {"type": type(e).__name__, "message": f"回應無法序列化：{e}"}})
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass


def make_server(platform, *, host="127.0.0.1", port=0, secret=None):
    if host not in ("127.0.0.1", "localhost", "::1") and not secret:
        raise ValueError("非本機綁定必須設定 EMFORGE_PLATFORM_TOKEN")
    server = Server((host, port), Handler)
    server.platform, server.secret = platform, secret
    return server


@contextlib.contextmanager
def serving(platform, *, host="127.0.0.1", port=0, secret=None):
    server = make_server(platform, host=host, port=port, secret=secret)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.05), daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
