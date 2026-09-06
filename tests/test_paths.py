# -*- coding: utf-8 -*-
"""tests/test_paths.py — `emforge/paths.py`：磁碟命名的唯一真相源。

防什麼：檔名／目錄名在各模組各自拼字串然後慢慢漂（舊 repo 的 `<store>_input` 在 8 處手拼）。
這裡用快照釘死所有名字；改名＝有意識地改這條測試。

M12b 起分兩種回傳值：共享狀態＝ depot **key**（POSIX 相對字串、不吃 root、目錄前綴帶尾 `/`）；
本機路徑（registry.py／strategies/*.py／策略工作目錄）＝ `Path`、仍吃 root。
"""
from pathlib import Path

import pytest

from emforge import paths


def test_key_snapshot():
    got = paths.snapshot(profile="p", store="s", rec_id="0123456789abcdef", spec="v", tag="216")
    assert got == {
        "db_dir": "db/p/",
        "record_file": "db/p/0123456789abcdef-s.npz",
        "db_index": "db/p/_index.jsonl",
        "db_imported": "db/p/_imported.json",
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
        "batch_results_dir": "batches/s/results/",
        "batch_result": "batches/s/results/0123456789abcdef.json",
        "runtime_lock": "runtime_state/p/lock",
        "strategies_yaml": "runtime_state/p/strategies.yaml",
        "state_json": "runtime_state/p/state.json",
        "status_json": "runtime_state/p/status.json",
        "events_jsonl": "runtime_state/p/events.jsonl",
        "pending_jsonl": "runtime_state/p/pending.jsonl",
        "runtime_stop": "runtime_state/p/STOP",
        "control_json": "runtime_state/p/control.json",
        "inflight_dir": "runtime_state/p/inflight/",
        "inflight_file": "runtime_state/p/inflight/s.json",
    }


def test_keys_obey_depot_key_rules():
    """Depot.check_key 的規則：非空、不以 `/` 開頭、無反斜線；目錄前綴才以 `/` 結尾。"""
    from emforge.depot import Depot
    snap = paths.snapshot(profile="p", store="s", rec_id="0123456789abcdef", spec="v", tag="216")
    for name, key in snap.items():
        assert isinstance(key, str) and "\\" not in key, f"{name}={key!r}"
        (Depot.check_prefix if name.endswith("_dir") else Depot.check_key)(key)
    for prefix in paths.layout_prefixes():
        Depot.check_prefix(prefix)


def test_layout_prefixes_are_the_ones_init_creates():
    """`emforge init` 的 ensure_prefixes 清單；`strategies/` 不在裡面——它是本機程式碼目錄。"""
    assert set(paths.layout_prefixes()) == {"db/", "ledger/", "queue/", "queue/state/", "queue/log/",
                                            "batches/", "runtime_state/"}


def test_local_snapshot_is_paths_under_root():
    r = Path("R")
    got = {k: v.relative_to(r).as_posix() for k, v in paths.local_snapshot(r, profile="p", strategy="k").items()}
    assert got == {
        "registry_py": "registry.py",
        "user_strategies_dir": "strategies",
        "strategy_workdir": "runtime_state/p/strategies/k",
    }


def test_local_paths_survive_apostrophe_and_cjk_root(root):
    """NAS 真實路徑含 `'` 與中文；本機那一節要能建目錄、寫檔、讀回。"""
    assert "'" in str(root)
    f = paths.user_strategies_dir(root) / "blind.py"
    f.parent.mkdir(parents=True)
    f.write_text("# x\n", encoding="utf-8")
    assert f.read_text(encoding="utf-8") == "# x\n"


def test_names_parsed_back_from_listing_keys():
    """列舉回來的 key 要能反推名字：紀錄主幹、結果 id、子目錄名。"""
    assert paths.record_stems(["db/p/aa-s1.npz", "db/p/bb-s2.npz", "db/p/_index.jsonl", "db/p/sub/"]) == {"aa-s1", "bb-s2"}
    assert paths.result_ids(["batches/s/results/a1.json", "batches/s/results/b2.json"]) == {"a1", "b2"}
    assert paths.dir_names(["db/p1/", "db/p2/", "db/x.json"]) == ["p1", "p2"]
    assert paths.record_by_stem("p", paths.record_stem("aa", "s1")) == paths.record_file("p", "aa", "s1")


def test_store_name_format_and_notarize_variant():
    assert paths.store_name("dual_p01_db075", "top_k_flip", 7) == "dual_p01_db075-top_k_flip-t00007"
    assert paths.notarize_store_name("dual_p01_db075", 7, "0123456789abcdef", 2) == "dual_p01_db075-notarize-t00007-01234567-r2"
    assert paths.smoke_store_name("p", "0123456789abcdef", 1, "20260901120000") == "p-smoke-01234567-20260901120000-r1"
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
