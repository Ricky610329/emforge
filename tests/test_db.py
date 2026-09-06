# -*- coding: utf-8 -*-
"""tests/test_db.py — `emforge/db.py`：一筆一檔資料庫、增量索引、唯讀 View。

防什麼：I-15（整份覆寫）、I-5（全史掃描撐爆）、D7（策略碰不到寫入）、跨 profile 只讀不寫。
"""
import numpy as np
import pytest

from emforge import db as dbm
from emforge import model, paths

P = "fake_f1"


def _rec(seed, store, *, profile=P, status="done", score=None, strategy="s", arm=None, tick=1, kind="sample",
         shape=(4, 4)):
    b = np.random.default_rng(seed).random(shape) > 0.5
    resp = None if status == "error" else np.full((2, 5), float(seed), np.float32)
    if score is None and status == "done":
        score = -float(seed)
    return model.Record(id=model.record_id(b, profile), sim_profile=profile, bits=b, response=resp,
                        measure={} if status == "error" else {"m1": score}, score=None if status == "error" else score,
                        status=status, strategy=strategy, arm=arm, parent=None, tick=tick, seed=seed, note={}, kind=kind,
                        run={"store": store, "machine": "216", "worker_ver": "v", "profile_hash": "h" * 12, "time_s": 1.0},
                        extra={})


# ── add ─────────────────────────────────────────────────────────────────────
def test_add_writes_one_file_named_id_dash_store(root):
    d = dbm.Database(root, write_profile=P)
    r = _rec(1, "st1")
    assert d.add(r) is True
    f = root / paths.record_file(P, r.id, "st1")
    assert f.exists() and f.suffix == ".npz"
    back = d.load(P, f.stem)
    assert back.id == r.id and np.array_equal(back.bits, r.bits) and np.array_equal(back.response, r.response)
    assert back.run == r.run and back.status == "done"


def test_add_same_id_same_store_returns_false_keeps_first(root):
    """回歸 I-15（2026-08-31）：多方各跑一次匯總、後者整份覆寫前者。一筆一檔＋永不覆寫＝合併語義。"""
    d = dbm.Database(root, write_profile=P)
    r = _rec(2, "st1", score=-2.0)
    assert d.add(r) is True
    r2 = _rec(2, "st1", score=-9.0)
    assert d.add(r2) is False
    assert d.load(P, f"{r.id}-st1").score == -2.0
    assert len(list((root / paths.db_dir(P)).glob("*.npz"))) == 1


def test_add_same_id_different_store_creates_second_file(root):
    d = dbm.Database(root, write_profile=P)
    r = _rec(3, "st1")
    assert d.add(r) and d.add(_rec(3, "notarize1", kind="repeat"))
    assert len(d.measurements(P, r.id)) == 2


def test_add_refuses_other_profile_when_write_bound(root):
    """§7：一個實例只寫自己綁的 profile；讀別的可以（見 View 測試）。"""
    d = dbm.Database(root, write_profile=P)
    with pytest.raises(dbm.ProfileWriteRefused):
        d.add(_rec(4, "st1", profile="other_p"))
    assert not (root / paths.db_dir("other_p")).exists()
    dbm.Database(root).add(_rec(4, "st1", profile="other_p"))  # 不綁定的實例（匯入器）可以


def test_add_rejects_id_that_does_not_match_bits_and_profile(root):
    d = dbm.Database(root, write_profile=P)
    r = _rec(5, "st1")
    bad = model.Record(**{**r.__dict__, "id": "0" * 16})
    with pytest.raises(ValueError, match="id"):
        d.add(bad)


# ── 索引 ────────────────────────────────────────────────────────────────────
def test_index_appended_not_rebuilt_on_add(root, monkeypatch):
    """回歸 I-5（2026-08-27）：全史查重表每次重建撐爆記憶體。入庫時 append 索引；開庫只補差集、不重載已知檔。"""
    loads = {"n": 0}
    d = dbm.Database(root, write_profile=P)
    real = d.depot.get_bytes

    def counting(key):
        loads["n"] += 1     # 讀一筆紀錄檔＝一次 get_bytes（索引走 read_log，不算）
        return real(key)

    monkeypatch.setattr(d.depot, "get_bytes", counting)
    for s in (10, 11, 12):
        d.add(_rec(s, "st1"))
    assert loads["n"] == 0, "add 不需要讀任何檔"
    assert len(d.metas(P)) == 3
    d2 = dbm.Database(d.depot)
    assert d2.refresh(P) == 0 and loads["n"] == 0, "索引完整 → 零載入"
    assert len(d2.metas(P)) == 3
    dbm.Database(root).add(_rec(13, "st2"))  # 另一個正常寫者：它自己會 append 索引
    assert d2.refresh(P) == 0 and loads["n"] == 0, "從索引檔合併即可，仍零載入"
    assert len(d2.metas(P)) == 4
    r = _rec(14, "st3")
    dbm._save_npz(d.depot, paths.record_file(P, r.id, "st3"), r)  # 有人直接搬檔進來、沒寫索引
    assert d2.refresh(P) == 1 and loads["n"] == 1, "只載入那一筆索引沒有的檔"
    assert len(d2.metas(P)) == 5


def test_index_picks_up_files_dropped_behind_its_back_and_drops_vanished(root):
    d = dbm.Database(root, write_profile=P)
    r = _rec(20, "st1")
    d.add(r)
    d.add(_rec(21, "st1"))
    (root / paths.record_file(P, r.id, "st1")).unlink()  # 有人手動刪檔（不該，但要能自癒）
    assert d.refresh(P) == 0
    assert {m["id"] for m in d.metas(P)} == {_rec(21, "st1").id}
    # 索引檔本身被刪 → 從檔重建
    (root / paths.db_index(P)).unlink()
    d3 = dbm.Database(root)
    assert d3.refresh(P) == 1 and len(d3.metas(P)) == 1


def test_refresh_does_not_compact_index_when_listing_comes_back_empty(monkeypatch):
    """列舉可最終一致（S3 式後端）：目錄短暫「看起來」空了不能把整份索引剔光——索引非空而列舉空 → 不壓實。"""
    from emforge.depot import MemoryDepot
    depot = MemoryDepot()
    d = dbm.Database(depot, write_profile=P)
    d.add(_rec(40, "st1"))
    d.add(_rec(41, "st1"))
    before = depot.read_log(paths.db_index(P))
    monkeypatch.setattr(depot, "list", lambda prefix: [])
    d2 = dbm.Database(depot)
    assert d2.refresh(P) == 0
    assert len(d2.metas(P)) == 2 and depot.read_log(paths.db_index(P)) == before, "索引一行都不動"


def test_ids_count_only_status_done_by_default(root):
    """去重只認量成功的：error 的 id 要能被再次提案。"""
    d = dbm.Database(root, write_profile=P)
    ok, err = _rec(30, "st1"), _rec(31, "st1", status="error")
    d.add(ok)
    d.add(err)
    assert d.ids(P) == {ok.id}
    assert d.ids(P, status=("done", "error")) == {ok.id, err.id}
    assert d.ids("nonexistent_profile") == set()


# ── View ────────────────────────────────────────────────────────────────────
def _seed_db(root):
    d = dbm.Database(root, write_profile=P)
    d.add(_rec(40, "a-t1", score=-5.0, strategy="blind", arm="blind", tick=1))
    d.add(_rec(41, "a-t1", score=-1.0, strategy="blind", arm="blind", tick=1))
    d.add(_rec(42, "b-t2", score=-2.0, strategy="top_k_flip", tick=2))
    d.add(_rec(42, "n-t3", score=-2.6, strategy="notarize", kind="repeat", tick=3))  # 同 id 重測、較差
    d.add(_rec(43, "b-t3", status="error", strategy="top_k_flip", tick=3))
    d.add(_rec(44, "b-t4", score=-0.5, strategy="top_k_flip", tick=4))
    return d


def test_view_top_unique_ids_conservative_min_score(root):
    """§3 ⑬：策略讀到的是保守值——同 id 多次量測取 min。"""
    v = _seed_db(root).view(P)
    top = v.top(3)
    assert [r.score for r in top] == [-0.5, -1.0, -2.6]
    assert top[2].id == _rec(42, "x").id and top[2].run["store"] == "n-t3"
    assert len({r.id for r in v.top(10)}) == len(v.top(10)) == 4, "error 不進榜、id 不重複"


def test_view_query_filters_strategy_arm_status_since_tick_limit(root):
    v = _seed_db(root).view(P)
    assert len(v.query()) == 6
    assert {r.strategy for r in v.query(strategy="blind")} == {"blind"} and len(v.query(strategy="blind")) == 2
    assert len(v.query(arm="blind")) == 2
    assert [r.status for r in v.query(status="error")] == ["error"]
    assert len(v.query(status=("done", "error"), since_tick=3)) == 3
    assert len(v.query(limit=2)) == 2
    ticks = [r.tick for r in v.query()]
    assert ticks == sorted(ticks), "預設依 tick 升冪"


def test_view_mine_requires_strategy_binding(root):
    d = _seed_db(root)
    with pytest.raises(ValueError):
        d.view(P).mine()
    mine = d.view(P, strategy="top_k_flip").mine()
    assert {r.strategy for r in mine} == {"top_k_flip"} and len(mine) == 3
    assert len(d.view(P, strategy="top_k_flip").mine(status="done", since_tick=4)) == 1


def test_view_measurements_returns_all_runs_of_an_id(root):
    v = _seed_db(root).view(P)
    ms = v.measurements(_rec(42, "x").id)
    assert sorted(r.run["store"] for r in ms) == ["b-t2", "n-t3"]
    assert v.measurements("ffffffffffffffff") == []


def test_view_has_no_write_methods(root):
    """D7：策略拿到的是 View——結構上沒有寫入方法，不是靠文件叮嚀。"""
    v = _seed_db(root).view(P)
    for name in ("add", "write", "remove", "delete", "save", "update"):
        assert not hasattr(v, name), name


def test_view_can_read_other_profile(root):
    _seed_db(root)
    dbm.Database(root).add(_rec(50, "o-t1", profile="other_p"))
    v = dbm.Database(root, write_profile=P).view(P)
    assert len(v.query(profile="other_p")) == 1
    assert v.top(1, profile="other_p")[0].sim_profile == "other_p"
    assert dbm.Database(root).profiles() == ["fake_f1", "other_p"]
