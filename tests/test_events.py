# -*- coding: utf-8 -*-
"""tests/test_events.py — `emforge/events.py`：事件白名單與 emit。

防什麼：事件名各處自由發明、欄位缺漏，讓 AI 層／人讀 events.jsonl 時要猜。
"""
import pytest

from emforge import events, fs, paths


def test_emit_rejects_unlisted_name_and_missing_required_fields(root):
    key = paths.events_jsonl("fake_f1")
    with pytest.raises(events.UnknownEvent):
        events.emit(root, key, "made_up_event", foo=1)
    with pytest.raises(events.EventFieldsMissing, match="reason"):
        events.emit(root, key, "batch_failed", store="s")
    assert not (root / key).exists(), "拒絕的事件不落檔"


def test_emit_appends_line_with_at_and_event_and_returns_dict(root):
    key = paths.events_jsonl("fake_f1")
    e = events.emit(root, key, "batch_failed", store="s", reason="x", extra_ok=1)
    assert e["event"] == "batch_failed" and e["store"] == "s" and len(e["at"]) == 19 and e["extra_ok"] == 1
    events.emit(root, key, "runtime_stop", reason="stop_file")
    lines = fs.read_jsonl(root / key)
    assert [ln["event"] for ln in lines] == ["batch_failed", "runtime_stop"]


def test_emit_accepts_a_depot_and_event_field_named_name_does_not_clash(root):
    """`depot`／`key`／`event` 都是 positional-only：策略事件的欄位就叫 `name`，不能撞。"""
    from emforge.depot import MemoryDepot
    d = MemoryDepot()
    key = paths.events_jsonl("fake_f1")
    events.emit(d, key, "strategy_loaded", name="top_k_flip")
    assert d.read_log(key) == [{"at": d.read_log(key)[0]["at"], "event": "strategy_loaded", "name": "top_k_flip"}]


def test_event_names_are_lower_snake_and_have_field_tuples():
    for name, fields in events.EVENTS.items():
        assert paths.is_valid_name(name), name
        assert isinstance(fields, tuple) and all(paths.is_valid_name(f) for f in fields), name
    assert {"batch_dispatched", "profile_tamper", "strategy_paused", "record_candidate", "worker_start", "job_yield"} <= set(events.EVENTS)
