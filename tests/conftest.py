# -*- coding: utf-8 -*-
"""tests/conftest.py — 全套測試的共用 fixture。

鐵則：**測試永不碰 NAS、不耦合這台機器的狀態**。
- 所有需要根目錄的測試一律用 `root` fixture（tmp_path 下）；session 開頭把整組 `EMFORGE_*` 從環境拿掉
  （檢查 #25：以前只拿 EMFORGE_ROOT——設了 EMFORGE_DEPOT 就把假資料灌進共享庫）。
- 體檢釘成固定值（檢查 #23：ansysedt 在跑／磁碟不足時 31 條紅＋1 條掛死；要測 True 的自己 monkeypatch 回來）。
- 行程級註冊表（measure／spec／profile／MemoryDepot）每個測試前後清空（檢查 #31）。
"""
import os
from pathlib import Path

import pytest

ENV_ISOLATED = ("EMFORGE_ROOT", "EMFORGE_DEPOT", "EMFORGE_WORK", "EMFORGE_MACHINE", "EMFORGE_DEVICE_TOKEN",
                "EMFORGE_MCP_HOST", "EMFORGE_MCP_PORT", "EMFORGE_PLATFORM_TOKEN", "EMFORGE_PLATFORM_PORT")


def pytest_report_header(config):
    on = bool(os.environ.get("EMFORGE_ANTENNA_REPO"))
    return ("adapter 綁定／COM 建構測試：" + ("啟用（EMFORGE_ANTENNA_REPO 已設）" if on
            else "跳過 6 條（未設 EMFORGE_ANTENNA_REPO；要全跑請設成 Antenna clone 路徑）"))   # 檢查 #44


@pytest.fixture(scope="session", autouse=True)
def _no_nas_env():
    """整個 session 清掉整組 EMFORGE_*，強迫所有測試走 `root` fixture／顯式參數。"""
    saved = {k: os.environ.pop(k) for k in ENV_ISOLATED if k in os.environ}
    yield
    os.environ.update(saved)


@pytest.fixture(autouse=True)
def _fresh_registries():
    """行程級全域：每個測試前後清空，避免測試互相污染、讓同行程重跑冪等。"""
    from emforge import profiles, specs
    from emforge.depot import memory as mem
    for clear in (specs.clear_registry, profiles.clear_registry, mem.clear_registry):
        clear()
    yield
    for clear in (specs.clear_registry, profiles.clear_registry, mem.clear_registry):
        clear()


@pytest.fixture(autouse=True)
def _pin_machine_health(monkeypatch):
    """體檢不看真機：ansysedt 沒在跑、磁碟 100 GB。"""
    from emforge import doctor
    monkeypatch.setattr(doctor, "ansysedt_running", lambda: False)
    monkeypatch.setattr(doctor, "_free_gb", lambda p: 100.0)


@pytest.fixture
def root(tmp_path) -> Path:
    """共用根目錄。故意含中文與撇號——NAS 真實路徑就長這樣（T:\\碩二_鄒穎麒's\\…），
    路徑處理若靠字串拼接在這裡就會炸。"""
    r = tmp_path / "碩二_x's" / "emforge"
    r.mkdir(parents=True)
    return r
