"""遠端平台呼叫；傳輸失敗不自動重送有副作用操作。"""
import os
from urllib import request, error
from . import wire


class RemoteFailure(RuntimeError):
    pass


class RemotePlatform:
    def __init__(self, url, *, token=None, timeout_s=30):
        self.url = url.rstrip("/")
        self.token = token if token is not None else os.environ.get("EMFORGE_PLATFORM_TOKEN")
        self.timeout_s = timeout_s

    def call(self, op, **params):
        return self.request("/rpc", op, params)

    def request(self, route, op, params):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = request.Request(self.url + route, data=wire.dumps({"op": op, "params": params}), headers=headers)
        try:
            with request.urlopen(req, timeout=self.timeout_s) as response:
                result = wire.loads(response.read())
        except error.HTTPError as e:
            if e.code in (401, 403):
                raise PermissionError("平台認證失敗") from None
            raise RemoteFailure(f"平台 HTTP {e.code}") from None
        if not result["ok"]:
            self._raise(result["error"])
        return result["value"]

    @staticmethod
    def _raise(doc):
        from ..depot import FsBusy, FsCorrupt, LockTimeout
        types = {"ValueError": ValueError, "TypeError": TypeError, "KeyError": KeyError,
                 "FileNotFoundError": FileNotFoundError, "PermissionError": PermissionError,
                 "FsBusy": FsBusy, "FsCorrupt": FsCorrupt, "LockTimeout": LockTimeout,
                 "OSError": OSError, "TimeoutError": TimeoutError}
        raise types.get(doc["type"], RemoteFailure)(doc["message"])
