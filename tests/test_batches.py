# -*- coding: utf-8 -*-
"""tests/test_batches.py — `emforge/batches.py`：一批的落地（manifest.json / patterns.npz / results/<id>.json）。

防什麼：I-15（整份結果檔互相覆寫）、I-5（每次全讀）、批寫一半就被 worker 撿走。
"""
import numpy as np
import pytest

from emforge import batches, model, paths, testing

P = testing.FAKE_PROFILE


def _pats(n, seed=0):
    rng = np.random.default_rng(seed)
    pats = rng.random((n, *P.shape)) > 0.5
    pats[:, P.fixed_on] = True
    return pats


def _manifest(store, ids, **over):
    m = dict(store=store, sim_profile=P.name, profile_hash=P.profile_hash, strategy="blind", tick=1, seed=7, prio=5,
             kind="sample", items=[dict(id=i, parent=None, arm="blind", note={}) for i in ids])
    m.update(over)
    return m


def test_write_manifest_and_patterns_roundtrip_packbits(root):
    pats = _pats(3)
    ids = [model.record_id(p, P.name) for p in pats]
    b = batches.Batch(root, "fake_f1-blind-t00001")
    assert not b.exists()
    b.write(_manifest(b.store, ids), pats, ids)
    assert b.exists() and b.n_items() == 3 and b.ids() == ids
    got = b.patterns()
    assert list(got) == ids and all(np.array_equal(got[i], p) for i, p in zip(ids, pats))
    assert got[ids[0]].dtype == bool
    m = b.manifest()
    assert m["profile_hash"] == P.profile_hash and m["items"][1]["id"] == ids[1]
    with np.load(root / paths.batch_patterns(b.store), allow_pickle=False) as z:
        assert z["packed"].shape == (3, 8) and z["packed"].dtype == np.uint8, "packbits：8×8 一筆 8 bytes"


def test_write_refuses_existing_store_and_mismatched_ids(root):
    pats = _pats(2)
    ids = [model.record_id(p, P.name) for p in pats]
    b = batches.Batch(root, "s1")
    b.write(_manifest("s1", ids), pats, ids)
    with pytest.raises(batches.BatchExists):
        b.write(_manifest("s1", ids), pats, ids)
    with pytest.raises(ValueError, match="ids"):
        batches.Batch(root, "s2").write(_manifest("s2", ids), pats, ids[:1])


def test_manifest_is_written_last(root, monkeypatch):
    """manifest.json 是「批完整」的標記：patterns 先落、manifest 最後——worker 看到 manifest 就一定有 patterns。"""
    order = []
    b = batches.Batch(root, "s")
    real = b.depot.put_bytes

    def spy(key, data):
        order.append(paths.stem_of(key))
        return real(key, data)

    monkeypatch.setattr(b.depot, "put_bytes", spy)
    pats = _pats(1)
    ids = [model.record_id(pats[0], P.name)]
    b.write(_manifest("s", ids), pats, ids)
    assert order == ["patterns", "manifest"], "patterns 先落、manifest 最後（put_json 也走 put_bytes）"


def test_result_files_are_per_id_merge_semantics(root):
    """回歸 I-15（2026-08-31）：多台各寫一份整批結果檔、後者覆蓋前者。逐筆檔＝以 id 為主鍵的合併。"""
    b = batches.Batch(root, "s")
    b.write_result("aaaa", {"id": "aaaa", "status": "done", "machine": "216"})
    b.write_result("bbbb", {"id": "bbbb", "status": "done", "machine": "218"})   # 另一台接管後寫的
    b.write_result("aaaa", {"id": "aaaa", "status": "error", "attempts": 2})     # 重試覆寫**同一筆**
    res = b.results()
    assert set(res) == {"aaaa", "bbbb"} and res["aaaa"]["attempts"] == 2 and res["bbbb"]["machine"] == "218"
    assert b.result_ids() == {"aaaa", "bbbb"}


def test_read_results_incremental_by_known_ids(root, monkeypatch):
    """I-5：collect 每 tick 只讀新檔——已知 id 的檔連開都不開。"""
    b = batches.Batch(root, "s")
    for i in ("a1", "b2", "c3"):
        b.write_result(i, {"id": i})
    opened = []
    real = b.depot.get_json

    def spy(key, *a, **k):
        opened.append(paths.stem_of(key))
        return real(key, *a, **k)

    monkeypatch.setattr(b.depot, "get_json", spy)
    got = b.results(known_ids={"a1"})
    assert set(got) == {"b2", "c3"} and sorted(opened) == ["b2", "c3"]
    assert batches.Batch(root, "nothing").results() == {} and batches.Batch(root, "nothing").result_ids() == set()


def test_newest_result_at_is_progress_signal(root):
    b = batches.Batch(root, "s")
    assert b.newest_result_at() is None
    b.write_result("a", {"id": "a"})
    assert b.newest_result_at() is not None
    b.depot.set_modified_at(paths.batch_result("s", "a"), 1_000_000)
    assert b.newest_result_at() == 1_000_000


def test_make_result_shares_base_for_done_error_and_bad_shape():
    """M13：批結果與儀器 ad-hoc 結果同一來源（`result_base`／`make_result`／`error_result`）。"""
    from emforge.model import SimResult
    base = batches.result_base("0" * 16, attempts=2, machine="216", worker_ver="v", profile_hash=P.profile_hash)
    assert base["id"] == "0" * 16 and base["attempts"] == 2 and len(base["at"]) == 19
    ok = batches.make_result(P, base, SimResult(response=np.zeros((2, 17), np.float32), time_s=0.0, extra={"k": 1}), 3.5)
    assert ok["status"] == "done" and ok["time_s"] == 3.5 and ok["extra"] == {"k": 1} and len(ok["response"]) == 2
    bad = batches.make_result(P, base, SimResult(response=np.zeros((3, 17), np.float32), time_s=1.0, extra={}), 1.0)
    assert bad["status"] == "error" and bad["error"].startswith("bad_response_shape")
    err = batches.error_result(base, "FakeFailure: x")
    assert err["status"] == "error" and err["error"] == "FakeFailure: x" and err["machine"] == "216"


def test_make_result_rejects_nonfinite_response_as_error():
    """回歸 I-29（2026-09-23）：防止 NaN／inf 響應（CSV 空值、對數 0）標成 done 並拿到有限分數。"""
    from emforge.model import SimResult
    base = batches.result_base("0" * 16, attempts=1, machine="216", worker_ver="v", profile_hash=P.profile_hash)
    resp = np.zeros((2, 17), np.float32)
    resp[0, 3] = np.nan
    r = batches.make_result(P, base, SimResult(response=resp, time_s=1.0, extra={}), 1.0)
    assert r["status"] == "error" and r["error"].startswith("nonfinite_response")
    resp[0, 3] = -np.inf
    assert batches.make_result(P, base, SimResult(response=resp, time_s=1.0, extra={}), 1.0)["status"] == "error"
    resp[0, 3] = -80.0
    assert batches.make_result(P, base, SimResult(response=resp, time_s=1.0, extra={}), 1.0)["status"] == "done"
