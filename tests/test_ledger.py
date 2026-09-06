# -*- coding: utf-8 -*-
"""tests/test_ledger.py — `emforge/ledger.py`：榜（checksum、promote 唯一寫路徑、history append-only）、待審、rescore。

D3：每個評估器版本一榜，舊榜凍結不刪；紅線：手改榜檔會被 checksum 抓到。
"""
import os

import numpy as np
import pytest

from emforge import db as dbm
from emforge import fs, ledger, model, paths, testing

P = testing.FAKE_PROFILE


def _rec(seed, store="s", score=None, status="done", kind="sample"):
    b = np.random.default_rng(seed).random(P.shape) > 0.5
    b[P.fixed_on] = True
    sc = -float(seed) if score is None else score
    return model.Record(id=model.record_id(b, P.name), sim_profile=P.name, bits=b,
                        response=None if status == "error" else np.zeros((2, 17), np.float32),
                        measure={} if status == "error" else {"m1": sc, "m2": sc + 1, "m3": sc + 2, "m4": sc + 3},
                        score=None if status == "error" else sc, status=status, strategy="blind", arm="blind",
                        parent=None, tick=1, seed=seed, note={}, kind=kind,
                        run={"store": store, "machine": "216", "worker_ver": "v", "profile_hash": P.profile_hash, "time_s": 1.0})


@pytest.fixture
def setup(root):
    testing.make_fake_root(root)
    d = dbm.Database(root, write_profile=P.name)
    recs = [_rec(1, score=-3.0), _rec(2, score=-1.0), _rec(3, score=-2.0)]
    for r in recs:
        d.add(r)
    d.add(_rec(2, store="n1", score=-1.2, kind="repeat"))       # id2 重測較差 → 保守值 -1.2
    pend = ledger.Pending(root, P.name)
    pend.append({"id": recs[1].id, "tick": 2, "scores": [-1.0, -1.2], "conservative": -1.2, "spread": 0.2, "stores": ["n1"], "at": "t"})
    return root, d, recs, pend


def test_promote_sets_best_appends_history_with_by(setup):
    root, d, recs, pend = setup
    lg = ledger.Ledger(root, P.name, "fake_v1")
    assert lg.best() is None and not lg.exists()
    best = lg.promote(recs[1].id, by="ricky", db=d, pending=pend, note="首任")
    assert best["id"] == recs[1].id and best["score"] == -1.2 and best["by"] == "ricky" and best["at"]
    doc = lg.read()
    assert doc["profile"] == P.name and doc["spec"] == "fake_v1" and doc["best"] == best
    assert len(doc["history"]) == 1 and doc["history"][0]["event"] == "promote" and doc["history"][0]["prev_id"] is None
    assert lg.best()["id"] == recs[1].id


def test_promote_refuses_id_not_in_db_or_not_in_pending_unless_force(setup):
    root, d, recs, pend = setup
    lg = ledger.Ledger(root, P.name, "fake_v1")
    with pytest.raises(ledger.UnknownRecord):
        lg.promote("f" * 16, by="ricky", db=d, pending=pend)
    with pytest.raises(ledger.NotPending):
        lg.promote(recs[2].id, by="ricky", db=d, pending=pend)
    best = lg.promote(recs[2].id, by="ricky", db=d, pending=pend, force=True, note="人工")
    assert best["force"] is True and best["score"] == -2.0
    with pytest.raises(ValueError):
        lg.promote(recs[1].id, by="", db=d, pending=pend)


def test_promote_with_other_spec_recomputes_score_from_measure(setup):
    """回歸 review（砍掉的 ledger.py:89）：promote --spec 別的規格時，分數要用那個 spec 對 db 量測重算，
    不能抄 pending 裡 profile 規格算出的 conservative 寫進另一個規格的榜。"""
    from emforge import specs
    root, d, recs, pend = setup
    specs.register_spec(model.Spec(name="fake_v2", labels=P.labels, measure=P.measure, axes=("m4",), offsets=(0.0,)))
    best = ledger.Ledger(root, P.name, "fake_v2").promote(recs[1].id, by="ricky", db=d, pending=pend)
    assert best["score"] == pytest.approx(-1.2 + 3), "m4 的保守值（min over 原始 −1+3 與重測 −1.2+3）"
    assert best["score"] != pend.get(recs[1].id)["conservative"]
    v1 = ledger.Ledger(root, P.name, "fake_v1").promote(recs[1].id, by="ricky", db=d, pending=pend)
    assert v1["score"] == -1.2, "profile 自己的 spec：與 pending 的保守值一致（同一把尺算的）"


def test_history_append_only_across_promotes(setup):
    root, d, recs, pend = setup
    lg = ledger.Ledger(root, P.name, "fake_v1")
    lg.promote(recs[2].id, by="a", db=d, pending=pend, force=True)
    lg.promote(recs[1].id, by="b", db=d, pending=pend)
    h = lg.history()
    assert [x["id"] for x in h] == [recs[2].id, recs[1].id]
    assert h[1]["prev_id"] == recs[2].id and h[1]["prev_score"] == -2.0 and h[1]["by"] == "b"
    assert lg.best()["id"] == recs[1].id


def test_ledger_checksum_detects_manual_edit(setup):
    root, d, recs, pend = setup
    lg = ledger.Ledger(root, P.name, "fake_v1")
    lg.promote(recs[1].id, by="ricky", db=d, pending=pend)
    raw = fs.read_json(root / paths.ledger_file(P.name, "fake_v1"))
    raw["best"]["score"] = 99.0
    fs.atomic_write_json(root / paths.ledger_file(P.name, "fake_v1"), raw)
    with pytest.raises(ledger.LedgerTamper):
        lg.read()
    with pytest.raises(ledger.LedgerTamper):
        lg.promote(recs[2].id, by="x", db=d, pending=pend, force=True)


def test_rescore_creates_new_spec_ledger_leaves_old_untouched(setup):
    """D3：換評估器＝新榜、零重量；舊榜一個 byte 都不動。"""
    root, d, recs, pend = setup
    old = ledger.Ledger(root, P.name, "fake_v1")
    old.promote(recs[1].id, by="ricky", db=d, pending=pend)
    before = (root / paths.ledger_file(P.name, "fake_v1")).read_bytes()
    v2 = model.Spec(name="fake_v2", labels=P.labels, measure=P.measure, axes=("m4",), offsets=(0.0,))
    out = ledger.rescore(root, P, v2, d, by="ricky")
    assert out["n"] == 3 and out["best"]["id"] == recs[1].id and out["best"]["score"] == pytest.approx(-1.2 + 3)
    new = ledger.Ledger(root, P.name, "fake_v2")
    assert new.best()["id"] == recs[1].id and new.history()[0]["event"] == "rescore"
    assert (root / paths.ledger_file(P.name, "fake_v1")).read_bytes() == before
    with pytest.raises(ledger.LedgerExists):
        ledger.rescore(root, P, v2, d, by="ricky")
    out2 = ledger.rescore(root, P, v2, d, by="ricky", force=True)
    assert out2["n"] == 3 and len(new.history()) == 2


def test_rescore_never_rewrites_record_files_and_needs_same_measure(setup):
    root, d, recs, pend = setup
    files = sorted((root / paths.db_dir(P.name)).glob("*.npz"))
    mt = [os.path.getmtime(f) for f in files]
    ledger.rescore(root, P, model.Spec(name="fake_v3", labels=P.labels, measure=P.measure, axes=("m1",), offsets=(5.0,)), d, by="x")
    assert [os.path.getmtime(f) for f in files] == mt
    with pytest.raises(ValueError, match="measure"):
        ledger.rescore(root, P, model.Spec(name="fake_v9", labels=P.labels, measure="other_m", axes=("m1",), offsets=(0.0,)), d, by="x")


def test_pending_append_list_has_get(root):
    pend = ledger.Pending(root, P.name)
    assert pend.list() == [] and not pend.has("a") and pend.get("a") is None
    pend.append({"id": "a", "conservative": -1.0})
    pend.append({"id": "a", "conservative": -0.5})
    assert pend.has("a") and pend.get("a")["conservative"] == -0.5 and len(pend.list()) == 2
