"""結果先落本機再補傳：包含真 HTTP 與回覆遺失。"""
from contextlib import nullcontext

import pytest

from emforge import paths, testing
from emforge.batches import Batch
from emforge.depot import MemoryDepot
from emforge.depot.http import HttpDepot
from emforge.platform.http_server import serving
from emforge.platform.service import Platform
from emforge.queue import Queue
from emforge.worker.batch import run_batch
from emforge.worker.workdir import WorkDir
from tests.worker.conftest import make_batch, make_job


@pytest.mark.parametrize("http", [False, True])
@pytest.mark.parametrize("ack_lost", [False, True])
def test_pending_result_survives_restart_and_is_not_simulated_twice(tmp_path, monkeypatch, http, ack_lost):
    """回歸 I-12（2026-09-12）：防止上傳前斷線或回覆遺失造成重複模擬。"""
    from emforge.worker.outbox import ResultPending
    backend = MemoryDepot()
    with serving(Platform(backend)) if http else nullcontext(None) as url:
        depot = HttpDepot(url) if http else backend
        ids = make_batch(depot, "pending", n=1)
        q = Queue(depot)
        q.add(make_job("pending"))
        job = q.pick("216")
        result_key = paths.batch_result(job.store, ids[0])
        original, writes, sims = depot.put_bytes, [], []

        def flaky(key, data):
            if key == result_key:
                writes.append(key)
                if len(writes) == 1:
                    if ack_lost:
                        original(key, data)
                    raise OSError("connection reset")
            return original(key, data)

        def factory(wd):
            sim = testing.FakeSimulator(workdir=str(wd), profile=testing.FAKE_PROFILE)
            sims.append(sim)
            return sim

        monkeypatch.setattr(depot, "put_bytes", flaky)
        work = WorkDir(tmp_path / "work")
        with pytest.raises(ResultPending):
            run_batch(q, Batch(depot, job.store), job, testing.FAKE_PROFILE, factory, "216", "test", work=work)
        assert len(work.local.list(paths.result_outbox_dir(depot.spec))) == 1
        restored = WorkDir(work.root)
        restored.sweep_all()
        assert run_batch(q, Batch(depot, job.store), job, testing.FAKE_PROFILE, factory,
                         "216", "test", work=restored) == "done"
        assert sum(s.calls["simulate"] for s in sims) == 1
        assert Batch(depot, job.store).results()[ids[0]]["attempts"] == 1
        assert len(writes) == (1 if ack_lost else 2)
        assert not restored.local.list(paths.result_outbox_dir(depot.spec))


def test_outbox_preserves_other_owner_and_does_not_overwrite_newer_result(tmp_path):
    """回歸 I-2（2026-09-12）：防止舊 worker 補傳覆寫接管者的新結果。"""
    from emforge.worker.outbox import ResultOutbox
    depot = MemoryDepot()
    ids = make_batch(depot, "pending", n=1)
    q = Queue(depot)
    q.add(make_job("pending"))
    q.pick("218")
    batch, work = Batch(depot, "pending"), WorkDir(tmp_path / "work")
    old = {"id": ids[0], "status": "done", "attempts": 1,
           "profile_hash": testing.FAKE_PROFILE.profile_hash, "machine": "216", "response": [[1]]}
    outbox = ResultOutbox(work.local, depot, "216")
    outbox.save(batch, ids[0], old)
    outbox.flush(q)
    assert not batch.results() and work.local.list(paths.result_outbox_dir(depot.spec))
    newer = {**old, "machine": "218", "response": [[2]]}
    batch.write_result(ids[0], newer)
    q.mark_done("pending", "218", n_done=1, n_error=0, error_ids=[])
    other = MemoryDepot()
    ResultOutbox(work.local, other, "216").flush(Queue(other))
    assert work.local.list(paths.result_outbox_dir(depot.spec)), "不同平台不可消費本機待傳結果"
    outbox.flush(q)
    assert batch.results()[ids[0]] == newer
    assert not work.local.list(paths.result_outbox_dir(depot.spec))


def _saved(work, depot, batch, rid, result):
    from emforge.worker.outbox import ResultOutbox
    outbox = ResultOutbox(work.local, depot, "216")
    outbox.save(batch, rid, result)
    return outbox


def test_outbox_done_beats_remote_error_with_same_or_higher_attempts(tmp_path):
    """回歸 I-15（2026-09-23）：防止本機的成功結果被接管者的 error 蓋掉後刪除（缺或錯不得覆蓋成功）。"""
    depot = MemoryDepot()
    ids = make_batch(depot, "pending", n=1)
    q = Queue(depot)
    q.add(make_job("pending"))
    q.pick("216")
    batch, work = Batch(depot, "pending"), WorkDir(tmp_path / "work")
    base = {"id": ids[0], "profile_hash": testing.FAKE_PROFILE.profile_hash, "attempts": 1}
    batch.write_result(ids[0], {**base, "status": "error", "machine": "218", "attempts": 2, "error": "boom"})
    outbox = _saved(work, depot, batch, ids[0], {**base, "status": "done", "machine": "216", "response": [[1]]})
    outbox.flush(q)
    assert batch.results()[ids[0]]["status"] == "done"
    assert not work.local.list(paths.result_outbox_dir(depot.spec))
    # 反向：本機 error 不蓋遠端 done，也不蓋 attempts 更高的 error
    batch.write_result(ids[0], {**base, "status": "error", "machine": "218", "attempts": 2, "error": "boom"})
    outbox = _saved(work, depot, batch, ids[0], {**base, "status": "error", "machine": "216", "error": "old"})
    outbox.flush(q)
    assert batch.results()[ids[0]]["attempts"] == 2 and batch.results()[ids[0]]["machine"] == "218"
    assert not work.local.list(paths.result_outbox_dir(depot.spec))


def test_incompatible_or_abandoned_outbox_entry_is_held_not_fatal(tmp_path):
    """回歸 I-35（2026-09-23）：防止一筆不相容／已 abandon 的待傳結果每圈拋例外，讓 worker 連錯十圈自行退出並擋住其他補傳（I-4 的維護路徑版）。"""
    from emforge.worker.outbox import ResultOutbox
    depot = MemoryDepot()
    ids = make_batch(depot, "bad", n=1)
    good_ids = make_batch(depot, "good", n=1, seed=3)
    gone_ids = make_batch(depot, "gone", n=1, seed=5)
    q = Queue(depot)
    for s in ("bad", "good", "gone"):
        q.add(make_job(s))
    for _ in range(3):
        q.pick("216")
    ph = testing.FAKE_PROFILE.profile_hash
    work = WorkDir(tmp_path / "work")
    held = []
    outbox = ResultOutbox(work.local, depot, "216", log=lambda ev, **f: held.append((ev, f)))
    outbox.save(Batch(depot, "bad"), ids[0], {"id": ids[0], "status": "done", "attempts": 1, "profile_hash": "0" * 12,
                                              "response": [[1]]})
    outbox.save(Batch(depot, "good"), good_ids[0], {"id": good_ids[0], "status": "done", "attempts": 1, "profile_hash": ph,
                                                     "response": [[1]]})
    q.mark_done("gone", "abandon:test", n_done=0, n_error=0, error_ids=[])
    outbox.save(Batch(depot, "gone"), gone_ids[0], {"id": gone_ids[0], "status": "done", "attempts": 1, "profile_hash": ph,
                                                     "response": [[1]]})
    outbox.flush(q)                                     # 不拋
    assert Batch(depot, "good").results()[good_ids[0]]["status"] == "done", "不相容的一筆不能擋住其他補傳"
    assert not work.local.list(paths.result_outbox_dir(depot.spec)), "待傳區清空：好的送出、壞的移走"
    kept = work.local.list(paths.result_outbox_held_dir(depot.spec))
    assert len(kept) == 2, "不相容與 abandon 的證據保留在 held 分區"
    reasons = sorted(f["reason"] for ev, f in held if ev == "outbox_held")
    assert len(reasons) == 2 and any("不相容" in r for r in reasons) and any("abandon" in r for r in reasons)
    docs = [work.local.require_json(k) for k in kept]
    assert all({"depot", "store", "id", "result", "reason", "held_at"} <= d.keys() for d in docs)
    outbox.flush(q)                                     # 第二圈：held 的不再處理、不再取 jobs.lock
    assert len(work.local.list(paths.result_outbox_held_dir(depot.spec))) == 2


def test_flush_failure_before_claim_is_result_pending_not_batch_failure(tmp_path, monkeypatch):
    """回歸 I-35（2026-09-23）：防止認領時補傳遇瞬斷被當成這批的失敗（列入 fail 名單）；契約：上傳失敗不列失敗名單。"""
    from emforge.worker import batch as batch_mod
    from emforge.worker.outbox import ResultPending
    depot = MemoryDepot()
    make_batch(depot, "s", n=1)
    q = Queue(depot)
    q.add(make_job("s"))
    job = q.pick("216")

    def broken_flush(self, queue, store=None):
        raise OSError(64, "network name deleted")

    monkeypatch.setattr(batch_mod.ResultOutbox, "flush", broken_flush)
    with pytest.raises(ResultPending):
        run_batch(q, Batch(depot, job.store), job, testing.FAKE_PROFILE,
                  lambda wd: testing.FakeSimulator(workdir=str(wd), profile=testing.FAKE_PROFILE), "216", "test",
                  work=WorkDir(tmp_path / "work"))
    assert q.claim_owner("s") == "216" and q.state("s") == "claimed"


def test_outbox_held_event_passes_the_events_whitelist(tmp_path):
    """回歸 I-35（2026-09-23）：`outbox_held` 要在 events.py 白名單——以前不在，經 events.emit 記錄時拋 UnknownEvent，
    held 路徑等於還是每圈 worker_error（測試用 lambda 當 log 所以沒抓到）。"""
    from emforge import events
    from emforge.worker.outbox import ResultOutbox
    depot = MemoryDepot()
    ids = make_batch(depot, "bad", n=1)
    q = Queue(depot)
    q.add(make_job("bad"))
    q.pick("216")
    work = WorkDir(tmp_path / "work")
    log_key = paths.worker_log("216")
    outbox = ResultOutbox(work.local, depot, "216", log=lambda ev, **f: events.emit(depot, log_key, ev, **f))
    outbox.save(Batch(depot, "bad"), ids[0], {"id": ids[0], "status": "done", "attempts": 1, "profile_hash": "0" * 12,
                                              "response": [[1]]})
    outbox.flush(q)                                     # 不拋
    assert [e["event"] for e in depot.read_log(log_key)] == ["outbox_held"]
    assert len(work.local.list(paths.result_outbox_held_dir(depot.spec))) == 1
