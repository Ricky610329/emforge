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


def antenna_binding_status() -> tuple:
    """(True, 訊息)＝綁到了；(None, 訊息)＝沒設；(False, 訊息)＝設了但不是 Antenna repo。
    #! 回歸 I-30（2026-09-23）：以前只看環境變數有沒有設——指到不存在的路徑表頭照樣寫「啟用」，adapter 測試其實全 skip。"""
    raw = os.environ.get("EMFORGE_ANTENNA_REPO")
    if not raw:
        return None, "跳過 adapter 綁定／COM 建構測試（未設 EMFORGE_ANTENNA_REPO；要全跑請設成 Antenna clone 路徑）"
    from emforge.adapters.antenna import _bind
    root = _bind.repo_root()
    if root is None:
        return False, f"EMFORGE_ANTENNA_REPO={raw!r} 找不到 antenna/__init__.py——不是 Antenna repo，adapter 測試會全部 skip"
    return True, f"adapter 綁定／COM 建構測試：啟用（{root}）"


def pytest_report_header(config):
    return antenna_binding_status()[1]                                                       # 檢查 #44


def pytest_sessionstart(session):
    ok, text = antenna_binding_status()
    if ok is False:
        raise pytest.UsageError(text)                       # 設錯就整套拒跑，不讓「全綠」誤導


def pytest_collection_modifyitems(config, items):
    """`hfss` 標記＝要真 HFSS：只在 EMFORGE_HFSS_TESTS=1 跑（正式機手動），其餘一律 skip。"""
    if os.environ.get("EMFORGE_HFSS_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="需要真 HFSS（EMFORGE_HFSS_TESTS=1 才跑）")
    for item in items:
        if "hfss" in item.keywords:
            item.add_marker(skip)


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
