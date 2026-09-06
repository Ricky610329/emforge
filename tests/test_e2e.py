# -*- coding: utf-8 -*-
"""tests/test_e2e.py — 端到端：兩個策略 ＋ 行程內假 worker → 資料庫 → 公證 → 待審 → promote → 報表。

這是「核心閉環成立」的證明：architecture.md §3「一個候選的一生」每一步都在這裡走一遍（用 FakeSimulator）。
"""
import dataclasses
import subprocess
import sys
import textwrap

import pytest

from emforge import cli, fs, ledger, paths, profiles, strategy, testing
from emforge.db import Database, ProfileWriteRefused
from emforge.runtime.core import Runtime
from tests.runtime.conftest import make_rt, write_yaml

P = testing.FAKE_PROFILE


def _events(root, profile="fake_f1"):
    return [e["event"] for e in fs.read_jsonl(root / paths.events_jsonl(profile))]


def test_two_strategies_fake_worker_db_pending_promote_report(root, capsys):
    testing.make_fake_root(root)
    write_yaml(root)
    rt = make_rt(root)
    rt.acquire_lock()
    try:
        rt.tick()                                            # tick1：db 空 → top_k_flip 回空、blind 派一批
        assert _events(root).count("batch_dispatched") == 1 and "strategy_empty" in _events(root)
        testing.run_all_jobs(root)
        rt.tick()                                            # tick2：收 blind → 破榜候選 → 重測 ×2；top_k_flip 派
        ev = _events(root)
        assert "record_candidate" in ev and "notarize_dispatched" in ev
        assert any(s.startswith("fake_f1-top_k_flip-t00002") for s in [i["store"] for i in rt.inflight()])
        testing.run_all_jobs(root)
        rt.tick()                                            # tick3：收重測 → 一致 → pending；收 top_k_flip
        pend = ledger.Pending(root, "fake_f1").list()
        assert len(pend) >= 1 and "notarize_pass" in _events(root)
        assert not list((root / "ledger").rglob("*.json")), "runtime 從不寫榜"
    finally:
        rt.release_lock()
    cand = pend[0]["id"]
    assert cli.main(["promote", cand, "--root", str(root), "--profile", "fake_f1", "--by", "ricky"]) == 0
    assert ledger.Ledger(root, "fake_f1", "fake_v1").best()["id"] == cand
    assert cli.main(["report", "--root", str(root), "--profile", "fake_f1"]) == 0
    out = capsys.readouterr().out
    assert "blind" in out and "top_k_flip" in out and "notarize" in out
    db = Database(root)
    n_done = len(db.ids("fake_f1"))
    assert n_done >= 3 + 4, "blind 3 ＋ top_k_flip 4（重測是同 id 不算新設計）"
    assert len(db.measurements("fake_f1", cand)) == 3, "原始 ＋ 兩次重測"
    st = fs.read_json(root / paths.status_json("fake_f1"))
    assert st["tick"] == 3 and st["pending_count"] == len(pend) and st["db"]["n_done"] >= 7


def test_restart_mid_batch_resumes_without_duplicate_records(root):
    """回歸 I-12／I-14：runtime 中途死掉再起，reconcile 乾淨、續收、不重複入庫。"""
    testing.make_fake_root(root)
    write_yaml(root, "profile: fake_f1\nstrategies:\n  - {name: blind, prio: 9, batch: 3}\n")
    rt = make_rt(root)
    rt.acquire_lock()
    rt.tick()
    rt.release_lock()
    inflight = rt.inflight()[0]
    store, ids = inflight["store"], inflight["ids"]
    from emforge.batches import Batch
    Batch(root, store).write_result(ids[0], {"id": ids[0], "status": "done", "response": [[-12.0] * 17, [-3.0] * 17],
                                             "time_s": 1.0, "machine": "216", "worker_ver": "v",
                                             "profile_hash": P.profile_hash, "extra": {}, "at": "t", "attempts": 1})
    rt.acquire_lock()
    rt.tick()                                                # 收到 1 筆就「死」
    rt.release_lock()
    assert len(Database(root).ids("fake_f1")) == 1
    testing.run_all_jobs(root)                               # worker 照常跑完剩下的（第一筆已 done 會跳過）
    rt2 = make_rt(root)                                      # 重啟：state 重讀、對帳
    assert rt2.run(once=True) == 0
    assert len(Database(root).ids("fake_f1")) == 3
    added = [e for e in fs.read_jsonl(root / paths.events_jsonl("fake_f1")) if e["event"] == "record_added"]
    assert sum(1 for e in added if e["kind"] == "sample") == 3, "三筆樣本各入庫一次（重測是另外的 kind=repeat）"
    assert not (root / paths.inflight_file("fake_f1", store)).exists()
    assert rt2.state["tick"] == 3


def test_full_loop_on_memory_depot(root, capsys):
    """M12d：整條迴圈（init→run→worker→collect→notarize→promote→report）在 `memory://` 上跑通——
    磁碟上只有本機程式碼（registry.py、策略 workdir），db／queue／batches／ledger 一個都不落地。"""
    from emforge.depot import MemoryDepot
    from tests.runtime.conftest import YAML
    depot = MemoryDepot()
    testing.make_fake_root(root, depot=depot)
    depot.put_bytes(paths.strategies_yaml("fake_f1"), YAML.encode("utf-8"))
    rt = Runtime(root, "fake_f1", depot=depot, sleep=lambda s: None, propose_fn=strategy.propose_in_process)
    rt.acquire_lock()
    try:
        rt.tick()
        testing.run_all_jobs(root, depot=depot)
        rt.tick()
        testing.run_all_jobs(root, depot=depot)
        rt.tick()
    finally:
        rt.release_lock()
    pend = ledger.Pending(depot, "fake_f1").list()
    ev = [e["event"] for e in depot.read_log(paths.events_jsonl("fake_f1"))]
    assert pend and "notarize_pass" in ev and "record_added" in ev
    cand = pend[0]["id"]
    assert cli.main(["promote", cand, "--root", str(root), "--depot", depot.spec, "--profile", "fake_f1", "--by", "ricky"]) == 0
    assert ledger.Ledger(depot, "fake_f1", "fake_v1").best()["id"] == cand
    assert cli.main(["report", "--root", str(root), "--depot", depot.spec, "--profile", "fake_f1"]) == 0
    assert "blind" in capsys.readouterr().out
    for local_only_absent in ("db", "queue", "batches", "ledger"):
        assert not (root / local_only_absent).exists(), f"{local_only_absent} 不該落地"
    assert paths.registry_py(root).exists()


def test_core_e2e_never_imports_torch(root):
    testing.make_fake_root(root)
    write_yaml(root, "profile: fake_f1\nstrategies:\n  - {name: blind, prio: 9, batch: 2}\n")
    code = textwrap.dedent(f"""
        import sys
        from emforge import strategy, testing
        from emforge.runtime.core import Runtime
        rt = Runtime(r"{root}", "fake_f1", sleep=lambda s: None, propose_fn=strategy.propose_in_process)
        rt.acquire_lock(); rt.tick(); rt.release_lock()
        testing.run_all_jobs(r"{root}")
        rt.acquire_lock(); rt.tick(); rt.release_lock()
        print("torch" in sys.modules, "antenna" in sys.modules, len(rt.db.ids("fake_f1")))
    """)
    out = subprocess.run([sys.executable, "-X", "utf8", "-c", code], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=300)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "False False 2", out.stdout


def test_second_profile_instance_reads_but_never_writes_first(root):
    testing.make_fake_root(root)
    write_yaml(root)
    p2 = dataclasses.replace(P, name="fake_f2")
    profiles.register_profile(p2)
    paths.registry_py(root).write_text(
        "import dataclasses\nfrom emforge import profiles, testing\ntesting.register_fakes()\n"
        "profiles.register_profile(dataclasses.replace(testing.FAKE_PROFILE, name='fake_f2'))\n", encoding="utf-8")
    #? fake_f2 的 blind 用前景 prio：佇列是全機共用的，fake_f1 的 job 排隊中時背景策略依 D5 不派（正確行為）
    write_yaml(root, "profile: fake_f2\nstrategies:\n  - {name: blind, prio: 3, batch: 2}\n", profile="fake_f2")
    rt1, rt2 = make_rt(root), Runtime(root, "fake_f2", sleep=lambda s: None, propose_fn=strategy.propose_in_process)
    rt1.acquire_lock()
    rt2.acquire_lock()
    rt1.tick()
    rt2.tick()
    rt1.release_lock()
    rt2.release_lock()
    testing.run_all_jobs(root)
    rt1.acquire_lock()
    rt1.tick()
    rt1.release_lock()
    rt2.acquire_lock()
    rt2.tick()
    rt2.release_lock()
    db = Database(root)
    assert len(db.ids("fake_f1")) == 3 and len(db.ids("fake_f2")) == 2
    assert len(db.view("fake_f2").query(profile="fake_f1")) == 3, "讀別的 profile 可以"
    rec = db.view("fake_f1").query()[0]
    with pytest.raises(ProfileWriteRefused):
        rt2.db.add(rec)
    assert sorted(db.profiles()) == ["fake_f1", "fake_f2"]
