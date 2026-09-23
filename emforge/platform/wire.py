"""HTTP JSON 編解碼：bytes 只用於 Depot/程式碼包；NaN／±inf 走標記（I-25：File／Memory 存得下，HTTP 不能斷線）。"""
import base64
import json
import math

_NONFINITE = {"nan": float("nan"), "inf": float("inf"), "-inf": float("-inf")}


def encode(value):
    if isinstance(value, bytes):
        return {"__emforge_bytes__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, float) and not math.isfinite(value):
        return {"__emforge_float__": "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")}
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [encode(v) for v in value]
    return value


def decode(value):
    if isinstance(value, dict):
        if set(value) == {"__emforge_bytes__"}:
            return base64.b64decode(value["__emforge_bytes__"], validate=True)
        if set(value) == {"__emforge_float__"}:
            return _NONFINITE[value["__emforge_float__"]]
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


def dumps(value):
    return json.dumps(encode(value), ensure_ascii=False, allow_nan=False).encode("utf-8")


def loads(raw):
    return decode(json.loads(raw))
