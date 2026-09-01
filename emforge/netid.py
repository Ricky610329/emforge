"""emforge/netid.py — 機器 tag：`EMFORGE_MACHINE` 環境變數，否則本機 IP 末段（三台正式機一直用 216／218／37 稱呼）。

釘選比對用 tag **完全相等**（2026-08-03：末段 vs 完整 IP 比對錯 → 全機略過）。
"""
import os
import socket


def local_ip() -> str:
    """UDP connect 技巧取對外介面 IP；離線回 127.0.0.1。不 import 任何領域套件。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def local_tag() -> str:
    env = os.environ.get("EMFORGE_MACHINE")
    if env:
        return env
    return local_ip().rsplit(".", 1)[-1]
