"""佇列在每次認領時重看優先级，公平狀態經 Depot 持久化。"""
from emforge import batches, paths
from emforge.depot import MemoryDepot
from emforge.model import Job
from emforge.queue import Queue
from tests.test_queue import _batch, P


def add(q, store, strategy="search", run="run_a", prio=3, machine=None):
    _batch(q.depot, store)
    q.add(Job(store, P.name, P.profile_hash, prio, 2, machine=machine,
              extra={"strategy": strategy, "run_id": run}))


def finish(q, job, machine="216"):
    q.mark_done(job.store, machine, n_done=2, n_error=0, error_ids=[])


def test_equal_priority_rotates_algorithms_instead_of_draining_old_backlog():
    depot = MemoryDepot()
    q = Queue(depot)
    for i in range(3):
        add(q, f"a{i}", "alpha")
    add(q, "b0", "beta")
    first = q.pick("216")
    assert first.store == "a0"
    finish(q, first)
    assert Queue(depot).pick("216").store == "b0"


def test_equal_priority_rotates_runs_within_algorithm():
    q = Queue(MemoryDepot())
    add(q, "a0", run="run_a")
    add(q, "a1", run="run_a")
    add(q, "b0", run="run_b")
    finish(q, q.pick("216"))
    assert q.pick("216").store == "b0"


def test_urgent_overrides_fairness_and_does_not_preempt_live_claim():
    q = Queue(MemoryDepot())
    add(q, "background", prio=9)
    busy = q.pick("216")
    add(q, "urgent", prio=1)
    assert q.claim_owner(busy.store) == "216"
    assert q.pick("218").store == "urgent"
    assert q.claim_owner(busy.store) == "216"


def test_raise_priority_is_monotonic_and_keeps_original_job():
    q = Queue(MemoryDepot())
    add(q, "old", prio=9)
    assert q.raise_priority("old", 1)
    assert not q.raise_priority("old", 9)
    assert q.list()[0].prio == 1
    assert q.list(original=True)[0].prio == 9
    assert q.depot.require_json(paths.jobs_file())[0]["prio"] == 9
    assert batches.Batch(q.depot, "old").exists()


def test_pinned_foreground_does_not_block_other_machine_background():
    q = Queue(MemoryDepot())
    add(q, "urgent", prio=1, machine="216")
    add(q, "background", prio=9)
    assert q.pick("218").store == "background"
