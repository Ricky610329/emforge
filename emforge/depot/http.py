"""HTTP Depot：伺服器執行原子儲存操作；呼叫失敗不猜成功或失敗。"""
from .base import Depot
from ..platform.transport import RemotePlatform


class HttpDepot(Depot):
    def __init__(self, url, *, token=None):
        self.spec = url.rstrip("/")
        self.remote = RemotePlatform(self.spec, token=token)

    def _call(self, op, **params):
        return self.remote.request("/depot", op, params)

    def put_bytes(self, key, data):
        return self._call("put_bytes", key=self.check_key(key), data=data)

    def get_bytes(self, key):
        return self._call("get_bytes", key=self.check_key(key))

    def delete(self, key):
        return self._call("delete", key=self.check_key(key))

    def exists(self, key):
        return self._call("exists", key=self.check_key(key))

    def list(self, prefix):
        return self._call("list", prefix=self.check_prefix(prefix))

    def modified_at(self, key):
        return self._call("modified_at", key=self.check_key(key))

    def set_modified_at(self, key, epoch):
        return self._call("set_modified_at", key=self.check_key(key), epoch=epoch)

    def append(self, key, record):
        return self._call("append", key=self.check_key(key), record=record)

    def read_log(self, key):
        return self._call("read_log", key=self.check_key(key))

    def rewrite_log(self, key, records):
        return self._call("rewrite_log", key=self.check_key(key), records=list(records))

    def claim(self, key, payload):
        return self._call("claim", key=self.check_key(key), payload=self.check_lease_payload(payload))

    def owner(self, key):
        return self._call("owner", key=self.check_key(key))

    def touch(self, key):
        return self._call("touch", key=self.check_key(key))

    def release(self, key, owner=None):
        return self._call("release", key=self.check_key(key), owner=owner)

    def break_if_stale(self, key, stale_s, now=None):
        return self._call("break_if_stale", key=self.check_key(key), stale_s=stale_s, now=now)

    def ensure_prefixes(self, prefixes):
        return self._call("ensure_prefixes", prefixes=[self.check_prefix(p) for p in prefixes])

    def selfcheck(self):
        return self._call("selfcheck")

    def now(self):
        return self._call("now")
