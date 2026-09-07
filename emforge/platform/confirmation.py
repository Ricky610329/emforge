"""短效單次確認：綁定完整操作內容，供 agent 自動完成兩次呼叫。"""
import secrets
import threading
import time
from ..model import canonical_json

class Confirmation:
    def __init__(self, ttl_s=120):
        self.ttl_s, self._tokens, self._lock = ttl_s, {}, threading.Lock()

    def execute(self, op, payload, token, action):
        digest = canonical_json([op, payload])
        with self._lock:
            now = time.monotonic()
            self._tokens = {k: v for k, v in self._tokens.items() if v[1] > now}
            if token is None:
                token = secrets.token_urlsafe(24)
                self._tokens[token] = (digest, now+self.ttl_s)
                return {"needs_confirm": True, "token": token, "preview": {"operation": op, **payload}}
            if self._tokens.get(token, (None, 0))[0] != digest:
                raise ValueError("confirm_rejected: token 過期、用過或內容不同")
            del self._tokens[token]
        return action()
