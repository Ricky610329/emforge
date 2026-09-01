# -*- coding: utf-8 -*-
"""tests/worker/conftest.py — worker 測試共用：建批＋加 job＋認領。"""
import numpy as np
import pytest

from emforge import batches, model, queue, testing

P = testing.FAKE_PROFILE


def make_batch(root, store, n=3, seed=0, prio=5, strategy="blind"):
    pats = np.random.default_rng(seed).random((n, *P.shape)) > 0.5
    pats[:, P.fixed_on] = True
    ids = [model.record_id(p, P.name) for p in pats]
    batches.Batch(root, store).write(dict(store=store, sim_profile=P.name, profile_hash=P.profile_hash, strategy=strategy,
                                          tick=1, seed=seed, prio=prio, kind="sample",
                                          items=[dict(id=i, parent=None, arm=None, note={}) for i in ids]), pats, ids)
    return ids


def make_job(store, prio=5, machine=None, profile_hash=None):
    return model.Job(store=store, sim_profile=P.name, profile_hash=profile_hash or P.profile_hash, prio=prio, n=3,
                     machine=machine)


@pytest.fixture
def claimed(root):
    """(queue, batch, job, ids)：一批 3 筆、prio 5、已被 216 認領。"""
    testing.make_fake_root(root)
    ids = make_batch(root, "fake_f1-blind-t00001")
    q = queue.Queue(root)
    q.add(make_job("fake_f1-blind-t00001"))
    job = q.pick("216")
    return q, batches.Batch(root, job.store), job, ids
