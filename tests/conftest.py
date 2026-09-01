# -*- coding: utf-8 -*-
"""tests/conftest.py — 全套測試的共用 fixture。

鐵則：**測試永不碰 NAS**。所有需要根目錄的測試一律用 `root` fixture（tmp_path 下），
session 開頭把 `EMFORGE_ROOT` 從環境拿掉，避免任何 CLI 預設值意外指到 T:\。
"""
import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _no_nas_env():
    """整個 session 清掉 EMFORGE_ROOT，強迫所有測試走 `root` fixture。"""
    saved = os.environ.pop("EMFORGE_ROOT", None)
    yield
    if saved is not None:
        os.environ["EMFORGE_ROOT"] = saved


@pytest.fixture
def root(tmp_path) -> Path:
    """共用根目錄。故意含中文與撇號——NAS 真實路徑就長這樣（T:\\碩二_鄒穎麒's\\…），
    路徑處理若靠字串拼接在這裡就會炸。"""
    r = tmp_path / "碩二_x's" / "emforge"
    r.mkdir(parents=True)
    return r
