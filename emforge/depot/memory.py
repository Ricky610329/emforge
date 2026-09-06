"""emforge/depot/memory.py — `MemoryDepot`：`Depot` 的行程內後端（測試、單行程 demo）。

同名單例（`open_depot("memory://<name>")` 回同一實例；建構時自動註冊）；所有操作在一把 RLock 下＝執行緒安全；
時鐘可注入（`clock=`）讓陳舊／破鎖測試不用睡。子行程看不到——`run` 遇到它自動 in-process。
"""
import copy
import itertools
import json
import threading
import time

from ..fs import FsCorrupt
from .base import Depot

_REGISTRY: dict = {}
_REGISTRY_LOCK = threading.Lock()
_ANON = itertools.count(1)


def registered(name: str):
    with _REGISTRY_LOCK:
        return _REGISTRY.get(name)


class MemoryDepot(Depot):
    def __init__(self, name: str | None = None, clock=time.time):
        self.name = name or f"anon-{next(_ANON)}"
        self.spec = f"memory://{self.name}"
        self._clock = clock
        self._docs: dict = {}  # key → (bytes, mtime)
        self._logs: dict = {}  # key → (list[dict], mtime)
        self._lock = threading.RLock()
        with _REGISTRY_LOCK:
            _REGISTRY[self.name] = self

    def __repr__(self) -> str:
        return f"MemoryDepot({self.name!r})"

    def now(self):
        return self._clock()

    # ── 文件 ─────────────────────────────────────────────────────────────────
    def put_bytes(self, key, data):
        self.check_key(key)
        with self._lock:
            self._docs[key] = (bytes(data), self._clock())

    def get_bytes(self, key):
        self.check_key(key)
        with self._lock:
            d = self._docs.get(key)
            return d[0] if d else None

    def delete(self, key):
        self.check_key(key)
        with self._lock:
            return (self._docs.pop(key, None) is not None) or (self._logs.pop(key, None) is not None)

    def exists(self, key):
        self.check_key(key)
        with self._lock:
            return key in self._docs or key in self._logs

    # ── 列舉／時間 ───────────────────────────────────────────────────────────
    def list(self, prefix):
        self.check_prefix(prefix)
        out = set()
        with self._lock:
            keys = list(self._docs) + list(self._logs)
        for k in keys:
            if not k.startswith(prefix):
                continue
            head, sep, _ = k[len(prefix):].partition("/")
            if head.startswith("."):
                continue
            out.add(prefix + head + ("/" if sep else ""))
        return sorted(out)

    def modified_at(self, key):
        self.check_key(key)
        with self._lock:
            d = self._docs.get(key) or self._logs.get(key)
            return d[1] if d else None

    def set_modified_at(self, key, epoch):
        with self._lock:
            for table in (self._docs, self._logs):
                if key in table:
                    table[key] = (table[key][0], float(epoch))

    # ── 日誌 ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _normalize(record) -> dict:
        return json.loads(json.dumps(record, ensure_ascii=False, sort_keys=True))

    def append(self, key, record):
        self.check_key(key)
        rec = self._normalize(record)
        with self._lock:
            lst = self._logs.get(key, ([], 0.0))[0]
            lst.append(rec)
            self._logs[key] = (lst, self._clock())

    def read_log(self, key):
        self.check_key(key)
        with self._lock:
            d = self._logs.get(key)
            return copy.deepcopy(d[0]) if d else []

    def rewrite_log(self, key, records):
        self.check_key(key)
        recs = [self._normalize(r) for r in records]
        with self._lock:
            self._logs[key] = (recs, self._clock())

    # ── 租約 ─────────────────────────────────────────────────────────────────
    def claim(self, key, payload):
        self.check_key(key)
        self.check_lease_payload(payload)
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        with self._lock:
            if key in self._docs or key in self._logs:
                return False
            self._docs[key] = (data, self._clock())
            return True

    def owner(self, key):
        try:
            d = self.get_json(key)
        except FsCorrupt:
            return None
        return d if isinstance(d, dict) else None

    def touch(self, key):
        self.check_key(key)
        with self._lock:
            for table in (self._docs, self._logs):
                if key in table:
                    table[key] = (table[key][0], self._clock())
                    return True
            return False

    def release(self, key, owner=None):
        self.check_key(key)
        with self._lock:
            if key not in self._docs:
                return True
            if owner is not None:
                cur = self.owner(key)
                if cur is None or cur.get("owner") != owner:
                    return False
            del self._docs[key]
            return True

    def break_if_stale(self, key, stale_s, now=None):
        with self._lock:
            if not self.is_stale(key, stale_s, now):
                return False
            return self.delete(key)

    # ── 其他 ─────────────────────────────────────────────────────────────────
    def ensure_prefixes(self, prefixes):
        for prefix in prefixes:
            self.check_prefix(prefix)

    def selfcheck(self):
        return []
