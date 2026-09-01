# -*- coding: utf-8 -*-
"""tests/test_paths.py — `emforge/paths.py`：磁碟命名的唯一真相源。

防什麼：檔名／目錄名在各模組各自拼字串然後慢慢漂（舊 repo 的 `<store>_input` 在 8 處手拼）。
這裡用快照釘死所有路徑；改名＝有意識地改這條測試。
"""
from pathlib import Path

import pytest

from emforge import paths


def test_layout_snapshot_for_fixed_root():
    r = Path("R")
    got = {k: v.relative_to(r).as_posix() for k, v in paths.snapshot(r, profile="p", store="s", rec_id="0123456789abcdef",
                                                                     spec="v", tag="216", strategy="k").items()}
    assert got == {
        "registry_py": "registry.py",
        "user_strategies_dir": "strategies",
        "db_dir": "db/p",
        "record_file": "db/p/0123456789abcdef-s.npz",
        "db_index": "db/p/_index.jsonl",
        "retired_marker": "db/p/RETIRED",
        "ledger_file": "ledger/p/v.json",
        "jobs_file": "queue/jobs.json",
        "jobs_lock": "queue/jobs.lock",
        "claim_file": "queue/state/s.claim",
        "done_file": "queue/state/s.done",
        "fail_file": "queue/state/s.fail",
        "queue_stop": "queue/STOP",
        "queue_stop_tag": "queue/STOP.216",
        "worker_log": "queue/log/216.jsonl",
        "batch_manifest": "batches/s/manifest.json",
        "batch_patterns": "batches/s/patterns.npz",
        "batch_results_dir": "batches/s/results",
        "batch_result": "batches/s/results/0123456789abcdef.json",
        "runtime_lock": "runtime_state/p/lock",
        "strategies_yaml": "runtime_state/p/strategies.yaml",
        "state_json": "runtime_state/p/state.json",
        "status_json": "runtime_state/p/status.json",
        "events_jsonl": "runtime_state/p/events.jsonl",
        "pending_jsonl": "runtime_state/p/pending.jsonl",
        "runtime_stop": "runtime_state/p/STOP",
        "inflight_file": "runtime_state/p/inflight/s.json",
        "strategy_workdir": "runtime_state/p/strategies/k",
    }


def test_layout_dirs_are_the_ones_init_creates():
    r = Path("R")
    dirs = {p.relative_to(r).as_posix() for p in paths.layout_dirs(r)}
    assert dirs == {"db", "ledger", "queue", "queue/state", "queue/log", "batches", "runtime_state", "strategies"}


def test_root_with_apostrophe_and_cjk_works(root):
    """NAS 真實路徑含 `'` 與中文；每個 path 函式都要能建目錄、寫檔、讀回。"""
    assert "'" in str(root)
    f = paths.batch_result(root, "dual_p01_db075-blind-t00001", "0123456789abcdef")
    f.parent.mkdir(parents=True)
    f.write_text("{}", encoding="utf-8")
    assert f.read_text(encoding="utf-8") == "{}"
    for d in paths.layout_dirs(root):
        d.mkdir(parents=True, exist_ok=True)
        assert d.is_dir()


def test_store_name_format_and_notarize_variant():
    assert paths.store_name("dual_p01_db075", "top_k_flip", 7) == "dual_p01_db075-top_k_flip-t00007"
    assert paths.notarize_store_name("dual_p01_db075", 7, "0123456789abcdef", 2) == "dual_p01_db075-notarize-t00007-01234567-r2"
    #! store 名的 `-` 是欄位分隔：三個欄位拆回去要對得上
    assert paths.store_name("p", "s", 12345).split("-") == ["p", "s", "t12345"]


@pytest.mark.parametrize("name", ["blind", "top_k_flip", "dual_p01_db075", "a1"])
def test_is_valid_name_accepts_lower_snake(name):
    assert paths.is_valid_name(name)


@pytest.mark.parametrize("name", ["Top", "top-k", "1abc", "", "a b", "top_k_flip.py"])
def test_is_valid_name_rejects_dash_upper_digit_first_and_empty(name):
    assert not paths.is_valid_name(name)


def test_reserved_strategy_names_are_valid_names_but_reserved():
    """保留字本身符合命名規則（否則檢查根本不會撞到），只是不准當策略名。"""
    for n in paths.RESERVED_STRATEGY_NAMES:
        assert paths.is_valid_name(n)
    assert {"repeat", "notarize", "runtime", "cli"} <= paths.RESERVED_STRATEGY_NAMES
    assert "blind" not in paths.RESERVED_STRATEGY_NAMES, "blind 是內建策略名（兼保留 arm），可以當策略名"
