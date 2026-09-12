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
