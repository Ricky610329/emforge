# -*- coding: utf-8 -*-
"""tests/depot/test_memory.py — `MemoryDepot` 專屬：同名單例、隔離、時鐘注入、執行緒安全。"""
import threading

from emforge.depot import MemoryDepot, open_depot


def test_open_depot_memory_same_name_same_instance():
    a = open_depot("memory://alpha")
    b = open_depot("memory://alpha")
    c = open_depot("memory://beta")
    assert a is b and a is not c and a.spec == "memory://alpha"
    a.put_bytes("k", b"1")
    assert b.get_bytes("k") == b"1" and c.get_bytes("k") is None


def test_instances_are_isolated():
    a, b = MemoryDepot(name="x1"), MemoryDepot(name="x2")
    a.put_bytes("k", b"1")
    assert b.get_bytes("k") is None and b.list("") == []


def test_clock_injection_drives_modified_at_and_now():
    t = [1000.0]
    d = MemoryDepot(name="clock", clock=lambda: t[0])
    assert d.now() == 1000.0
    d.put_bytes("k", b"1")
    assert d.modified_at("k") == 1000.0
    t[0] = 2000.0
    assert d.touch("k") and d.modified_at("k") == 2000.0
    assert d.is_stale("k", 60) is False
    t[0] = 2100.0
    assert d.is_stale("k", 60) is True


def test_mixed_ops_thread_safe():
    d = MemoryDepot(name="threads")
    errors = []

    def work(i):
        try:
            for j in range(50):
                d.put_json(f"docs/{i}-{j}.json", {"i": i, "j": j})
                d.append(f"logs/{i}.jsonl", {"j": j})
                d.claim(f"leases/{i}", {"owner": str(i)})
                d.release(f"leases/{i}", owner=str(i))
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
    assert len(d.list("docs/")) == 400 and all(len(d.read_log(f"logs/{i}.jsonl")) == 50 for i in range(8))
    assert d.list("leases/") == []
