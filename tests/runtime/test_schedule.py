# -*- coding: utf-8 -*-
"""tests/runtime/test_schedule.py — `emforge/runtime/schedule.py`：靜態 prio、max_inflight、背景填空、策略例外→暫停。

回歸 I-4（2026-08-03）：策略炸了殺掉整個行程；D5：背景只在佇列空時；D9：連 3 次例外暫停策略、runtime 不死。
"""
import textwrap

import numpy as np

from emforge import batches, fs, paths, queue, strategy, testing
from emforge.runtime import collect as col
from emforge.runtime import schedule as sch
from tests.runtime.conftest import write_yaml

P = testing.FAKE_PROFILE


def _events(rt, name=None):
    ev = fs.read_jsonl(paths.events_jsonl(rt.root, "fake_f1"))
    return [e for e in ev if name is None or e["event"] == name]


def _stores(rt):
    return sorted(p.stem for p in paths.inflight_dir(rt.root, "fake_f1").glob("*.json"))


def test_schedule_dispatches_by_prio_and_records_inflight(rt):
    rt.state["tick"] = 1
    sch.schedule(rt)
    # db 空：top_k_flip 回空（strategy_empty）；佇列空 → 背景 blind 可以派
    assert _stores(rt) == ["fake_f1-blind-t00001"]
    assert [e["name"] for e in _events(rt, "strategy_empty")] == ["top_k_flip"]
    assert rt.state["strategies"]["blind"]["n_dispatched"] == 1


def test_schedule_skips_strategy_at_max_inflight(rt):
    rt.state["tick"] = 1
    sch.schedule(rt)
    rt.state["tick"] = 2
    sch.schedule(rt)
    assert _stores(rt) == ["fake_f1-blind-t00001"], "max_inflight=1：上批沒回來不再派"


def test_schedule_background_only_when_queue_empty_no_foreground_inflight(rt):
    """D5：背景填空只在整個佇列空、且沒有前景 inflight 時才跑。"""
    _seed_db(rt, 3)
    rt.state["tick"] = 1
    sch.schedule(rt)
    assert _stores(rt) == ["fake_f1-top_k_flip-t00001"], "前景派了 → 背景不派"
    testing.run_all_jobs(rt.root)
    col.collect(rt)
    rt.state["tick"] = 2
    sch.schedule(rt)
    assert "fake_f1-top_k_flip-t00002" in _stores(rt) and "fake_f1-blind-t00002" not in _stores(rt)
    testing.run_all_jobs(rt.root)
    col.collect(rt)
    y = write_yaml(rt.root, "profile: fake_f1\nstrategies:\n  - {name: blind, prio: 9, batch: 3}\n")
    import os, time
    os.utime(y, (time.time() + 5, time.time() + 5))
    rt.reload_config()
    rt.state["tick"] = 3
    sch.schedule(rt)
    assert _stores(rt) == ["fake_f1-blind-t00003"], "只剩背景、佇列空 → 背景派"


def test_three_consecutive_errors_pause_strategy_runtime_survives(rt):
    """回歸 I-4／D9：策略連 3 次例外 → 暫停該策略（操作保護），runtime 繼續跑別的。"""
    _user_strategy(rt.root, "boom", "COMPATIBLE = {'*'}\ndef propose(ctx):\n    raise RuntimeError('kaboom')")
    y = write_yaml(rt.root, "profile: fake_f1\nstrategies:\n  - {name: boom, prio: 3, batch: 2}\n"
                            "  - {name: blind, prio: 9, batch: 2}\n")
    import os, time
    os.utime(y, (time.time() + 5, time.time() + 5))
    rt.reload_config()
    for t in (1, 2, 3, 4):
        rt.state["tick"] = t
        sch.schedule(rt)
    st = rt.state["strategies"]["boom"]
    assert st["paused"] is True and st["errors_consecutive"] == 3
    errs = _events(rt, "strategy_error")
    assert [e["consecutive"] for e in errs] == [1, 2, 3] and "kaboom" in errs[0]["error"]
    assert [e["name"] for e in _events(rt, "strategy_paused")] == ["boom"]
    assert _stores(rt) == ["fake_f1-blind-t00001"], "runtime 活著，blind 照派（tick1 佇列空時）"


def test_timeout_counts_as_error_success_resets(rt, monkeypatch):
    calls = {"blind": 0}
    real = rt._propose

    def flaky(root, profile, name, **kw):
        if name == "blind":
            calls["blind"] += 1
            if calls["blind"] <= 2:
                raise strategy.StrategyTimeout("slow")
        return real(root, profile, name, **kw)

    monkeypatch.setattr(rt, "_propose", flaky)
    rt.state["tick"] = 1
    sch.schedule(rt)
    rt.state["tick"] = 2
    sch.schedule(rt)
    assert rt.state["strategies"]["blind"]["errors_consecutive"] == 2
    assert len(_events(rt, "strategy_timeout")) == 2
    rt.state["tick"] = 3
    sch.schedule(rt)
    assert rt.state["strategies"]["blind"]["errors_consecutive"] == 0 and not rt.state["strategies"]["blind"]["paused"]
    assert _stores(rt) == ["fake_f1-blind-t00003"]


def test_schedule_skips_disabled_and_paused_profile(rt):
    y = write_yaml(rt.root, "profile: fake_f1\nstrategies:\n  - {name: blind, prio: 9, batch: 2, enabled: false}\n")
    import os, time
    os.utime(y, (time.time() + 5, time.time() + 5))
    rt.reload_config()
    rt.state["tick"] = 1
    sch.schedule(rt)
    assert _stores(rt) == []
    y = write_yaml(rt.root, "profile: fake_f1\nstrategies:\n  - {name: blind, prio: 9, batch: 2}\n")
    os.utime(y, (time.time() + 10, time.time() + 10))
    rt.reload_config()
    rt.state["paused_profile"] = {"store": "x", "error_rate": 1.0}
    sch.schedule(rt)
    assert _stores(rt) == [], "profile 暫停 → 不派任何策略"


def test_schedule_survives_dispatch_failure_with_event(rt, monkeypatch):
    """回歸 review-3：dispatch 的例外（BatchExists／LockTimeout／NAS 斷線）不能殺 runtime，也不算策略的錯。"""
    def boom(*a, **k):
        raise RuntimeError("NAS 斷了")

    monkeypatch.setattr(sch, "dispatch", boom)
    rt.state["tick"] = 1
    sch.schedule(rt)                                      # 不拋
    ev = _events(rt, "dispatch_failed")
    assert ev and ev[0]["name"] == "blind" and "NAS" in ev[0]["error"] and ev[0]["tick"] == 1
    st = rt.state["strategies"]["blind"]
    assert st["errors_consecutive"] == 0 and st["paused"] is False, "派工失敗不是策略連敗"


def test_seed_recorded_and_same_tick_reproduces(rt):
    rt.state["tick"] = 5
    sch.schedule(rt)
    store = "fake_f1-blind-t00005"
    inf = fs.read_json(paths.inflight_file(rt.root, "fake_f1", store))
    seed = inf["seed"]
    assert seed == sch.strategy_seed(rt.state["seed_base"], "fake_f1", "blind", 5)
    again = strategy.propose_in_process(rt.root, P, "blind", budget=3, seed=seed, tick=5, params={})
    pats = batches.Batch(rt.root, store).patterns()
    assert all(np.array_equal(pats[i], p.pattern) for i, p in zip(inf["ids"], again))
    assert sch.strategy_seed(0, "fake_f1", "blind", 5) != sch.strategy_seed(0, "fake_f1", "blind", 6)
    assert sch.strategy_seed(0, "fake_f1", "blind", 5) != sch.strategy_seed(0, "fake_f1", "top_k_flip", 5)


def test_yaml_seed_overrides_derived(rt):
    y = write_yaml(rt.root, "profile: fake_f1\nstrategies:\n  - {name: blind, prio: 9, batch: 2, seed: 4242}\n")
    import os, time
    os.utime(y, (time.time() + 5, time.time() + 5))
    rt.reload_config()
    rt.state["tick"] = 1
    sch.schedule(rt)
    assert fs.read_json(paths.inflight_file(rt.root, "fake_f1", "fake_f1-blind-t00001"))["seed"] == 4242


def _seed_db(rt, n):
    from emforge import model
    rng = np.random.default_rng(99)
    for i in range(n):
        b = rng.random(P.shape) > 0.5
        b[P.fixed_on] = True
        rt.db.add(model.Record(id=model.record_id(b, P.name), sim_profile=P.name, bits=b,
                               response=np.zeros((2, 17), np.float32), measure={"m1": -float(i)}, score=-float(i),
                               status="done", strategy="blind", arm="blind", parent=None, tick=0, seed=0, note={},
                               kind="sample", run={"store": "seed", "machine": "x", "worker_ver": "v",
                                                  "profile_hash": P.profile_hash, "time_s": 1.0}))
    assert queue.Queue(rt.root).list() == []


def _user_strategy(root, name, src):
    d = paths.user_strategies_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.py").write_text(textwrap.dedent(src), encoding="utf-8")
