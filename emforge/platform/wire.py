"""HTTP JSON 編解碼：bytes 只用於 Depot/程式碼包。"""
import base64
import json


def encode(value):
    if isinstance(value, bytes):
        return {"__emforge_bytes__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [encode(v) for v in value]
    return value


def decode(value):
    if isinstance(value, dict):
        if set(value) == {"__emforge_bytes__"}:
            return base64.b64decode(value["__emforge_bytes__"], validate=True)
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


def dumps(value):
    return json.dumps(encode(value), ensure_ascii=False, allow_nan=False).encode("utf-8")


def loads(raw):
    return decode(json.loads(raw))
