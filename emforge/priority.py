"""人可讀優先級與持久公平輪替；不改寫不可變派工意圖。"""
from dataclasses import replace
from . import paths
from .model import canonical_json

LEVELS = {"urgent": 1, "normal": 3, "background": 9}


def value(priority, default=3):
    if priority is None:
        return default
    if not isinstance(priority, str) or priority not in LEVELS:
        raise ValueError("priority 必須為 urgent、normal 或 background")
    return LEVELS[priority]


def state(depot):
    return depot.get_json(paths.queue_scheduling()) or {"sequence": 0, "turns": {}, "boosts": {}}


def effective(job, scheduling):
    return replace(job, prio=min(job.prio, scheduling["boosts"].get(job.store, job.prio)))


def groups(job):
    strategy = job.extra.get("strategy", job.origin)
    run = job.extra.get("run_id") or strategy
    return canonical_json([job.sim_profile, strategy]), canonical_json([job.sim_profile, strategy, run])


def order(jobs, scheduling):
    def key(job):
        algorithm, run = groups(job)
        turns = scheduling["turns"]
        return job.prio, turns.get(algorithm, 0), turns.get(run, 0)
    return sorted(jobs, key=key)


def served(depot, scheduling, job):
    scheduling["sequence"] += 1
    for group in groups(job):
        scheduling["turns"][group] = scheduling["sequence"]
    depot.put_json(paths.queue_scheduling(), scheduling)
