"""昂貴模擬只在單筆結果保存後讓位，不重跑完成的 pattern。"""
import pytest
from emforge import batches, testing
from emforge.queue import Queue
from emforge.worker.batch import run_batch
from emforge.worker.workdir import WorkDir
from tests.test_priority import add


@pytest.mark.parametrize("incoming_prio", [1, 3])
def test_worker_yields_after_one_result_for_urgent_or_peer(root, incoming_prio):
    testing.make_fake_root(root)
    q = Queue(root)
    add(q, "original", strategy="alpha", prio=3)
    job = q.pick("216")
    sims = []

    class ArrivingSimulator(testing.FakeSimulator):
        def simulate(self, bits):
            result = super().simulate(bits)
            if self.calls["simulate"] == 1:
                add(q, "incoming", strategy="beta", prio=incoming_prio)
            return result

    def factory(wd):
        sim = ArrivingSimulator(workdir=str(wd), profile=testing.FAKE_PROFILE)
        sims.append(sim)
        return sim

    batch = batches.Batch(q.depot, job.store)
    outcome = run_batch(q, batch, job, testing.FAKE_PROFILE, factory, "216", "test",
                        work=WorkDir(root / "work"), sleep=lambda _: None)
    assert outcome == "yield"
    assert len(batch.results()) == 1
    assert all(r["status"] == "done" for r in batch.results().values())
    assert sims[0].calls["simulate"] == 1
    assert q.claim_owner(job.store) is None
    assert q.pick("216").store == "incoming"


def test_completed_single_pattern_does_not_requeue_only_to_yield(root):
    """已保存最後一筆就完成，不為新高優先工作重新開一次 HFSS。"""
    from emforge import paths
    testing.make_fake_root(root)
    q = Queue(root)
    add(q, "single", strategy="explore", prio=9)
    job = q.pick("216")
    batch = batches.Batch(q.depot, job.store)
    ids = batch.ids()
    # 預先完成其中一筆，只剩最後一筆；模擬期間有高優先工作抵達。
    batch.write_result(ids[0], {"id": ids[0], "status": "done", "attempts": 1})

    class Arrival(testing.FakeSimulator):
        def simulate(self, bits):
            result = super().simulate(bits)
            add(q, "urgent", prio=1)
            return result

    outcome = run_batch(q, batch, job, testing.FAKE_PROFILE,
                        lambda wd: Arrival(workdir=str(wd), profile=testing.FAKE_PROFILE),
                        "216", "test", work=WorkDir(root / "work"), sleep=lambda _: None)
    assert outcome == "done"
    assert all(r["status"] == "done" for r in batch.results().values())
    assert q.depot.owner(paths.claim_file(job.store))["owner"] == "216"
