# -*- coding: utf-8 -*-
"""tests/test_model.py — `emforge/model.py`：契約 dataclass（Profile/Spec/Proposal/Record/Job）與 record_id。

防什麼：舊 repo 完全沒有 schema（全靠慣例、真相散在 docstring）。這裡把欄位、去重鍵、hash 的依賴範圍釘死。
"""
import dataclasses

import numpy as np
import pytest

from emforge import model


def _bits(seed=0, shape=(4, 4)):
    return np.random.default_rng(seed).random(shape) > 0.5


def _profile(**over):
    base = dict(name="fake_f1", simulator="emforge.testing:FakeSimulator", geom_ver="fake1", kwargs={"a": 1, "b": [1, 2]},
                shape=(4, 4), labels=("L1", "L2"), n_points=5, fixed_on=np.zeros((4, 4), bool),
                measure="fake_m", spec="fake_v1", timeout_s=5)
    base.update(over)
    return model.Profile(**base)


# ── record_id / packbits ────────────────────────────────────────────────────
def test_record_id_is_16_hex_and_deterministic():
    b = _bits(1)
    a, b2 = model.record_id(b, "p"), model.record_id(b.copy(), "p")
    assert a == b2 and len(a) == 16 and int(a, 16) >= 0


def test_record_id_differs_by_profile_for_same_bits():
    """D6：era ≡ profile——同 bits 在不同儀器下是不同設計、各自要量。"""
    b = _bits(2)
    assert model.record_id(b, "dual_p01_db075") != model.record_id(b, "dual_p00")


def test_record_id_differs_when_one_bit_flips():
    b = _bits(3)
    c = b.copy()
    c[0, 0] = ~c[0, 0]
    assert model.record_id(b, "p") != model.record_id(c, "p")


def test_pack_unpack_bits_roundtrip_and_id_uses_packed_bytes():
    b = _bits(4, (5, 7))
    packed = model.pack_bits(b)
    assert packed.dtype == np.uint8 and packed.size == (35 + 7) // 8
    assert np.array_equal(model.unpack_bits(packed, (5, 7)), b)
    assert model.record_id(b, "p") == model.record_id(b.astype(np.float32), "p"), "0/1 float 與 bool 同一個 id"


# ── Profile ─────────────────────────────────────────────────────────────────
def test_profile_hash_depends_on_simulator_geom_kwargs_measure_only():
    p = _profile()
    h = p.profile_hash
    assert len(h) == 12
    same = dataclasses.replace(p, name="other", spec="x", timeout_s=999, retired=True)
    assert same.profile_hash == h, "名字／spec／逾時／退役都不是儀器的一部分"
    assert dataclasses.replace(p, kwargs={"a": 2, "b": [1, 2]}).profile_hash != h
    assert dataclasses.replace(p, geom_ver="fake2").profile_hash != h
    assert dataclasses.replace(p, simulator="x:Y").profile_hash != h
    assert dataclasses.replace(p, measure="other_m").profile_hash != h, "換尺＝換儀器"
    assert dataclasses.replace(p, kwargs={"b": [1, 2], "a": 1}).profile_hash == h, "鍵序無關（canonical JSON）"


def test_profile_validates_name_and_fixed_on_shape():
    with pytest.raises(ValueError):
        _profile(name="Bad-Name")
    with pytest.raises(ValueError):
        _profile(fixed_on=np.zeros((3, 3), bool))
    with pytest.raises(ValueError):
        _profile(spec="v-2")


def test_profile_is_frozen():
    p = _profile()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.name = "x"


# ── Spec ────────────────────────────────────────────────────────────────────
def test_spec_score_is_min_of_axes_plus_offsets_and_none_when_missing():
    s = model.Spec(name="dual_v2", labels=("S11", "S21", "S22"), measure="m", axes=("m1", "m2", "m3", "m4"),
                   offsets=(2.0, 2.0, 0.0, 5.0))
    assert s.score({"m1": -4.0, "m2": -3.0, "m3": -1.0, "m4": -8.0, "m5": -99.0}) == pytest.approx(-3.0)
    assert s.score({"m1": -4.0, "m2": -3.0, "m3": -1.0}) is None, "缺軸 → None，不猜"
    assert s.score({"m1": float("nan"), "m2": -3.0, "m3": -1.0, "m4": -8.0}) is None
    with pytest.raises(ValueError):
        model.Spec(name="v", labels=("a",), measure="m", axes=("m1", "m2"), offsets=(0.0,))


# ── Proposal ────────────────────────────────────────────────────────────────
def test_proposal_from_dict_rejects_missing_pattern_and_forbidden_keys():
    """D7：策略只能提 pattern/parent/arm/note；kind、sim_profile、score 都是 runtime 的欄位，多給就拒。"""
    b = _bits(5)
    p = model.Proposal.from_dict({"pattern": b, "parent": "abc", "note": {"d": 3}})
    assert p.parent == "abc" and p.arm is None and p.note == {"d": 3} and np.array_equal(p.pattern, b)
    with pytest.raises(model.ProposalError, match="pattern"):
        model.Proposal.from_dict({"parent": "abc"})
    for bad in ("kind", "sim_profile", "score", "id"):
        with pytest.raises(model.ProposalError, match=bad):
            model.Proposal.from_dict({"pattern": b, bad: "x"})
    assert model.Proposal.from_dict(p) is p, "已是 Proposal 就原樣回"


# ── Record ──────────────────────────────────────────────────────────────────
def _record(**over):
    b = _bits(6)
    base = dict(id=model.record_id(b, "fake_f1"), sim_profile="fake_f1", bits=b,
                response=np.arange(10, dtype=np.float32).reshape(2, 5),
                measure={"m1": -1.5, "m2": 0.25}, score=-1.5, status=model.STATUS_DONE, strategy="top_k_flip",
                arm=None, parent="0123456789abcdef", tick=7, seed=42, note={"d": 3}, kind=model.KIND_SAMPLE,
                run={"store": "fake_f1-top_k_flip-t00007", "machine": "216", "worker_ver": "emforge=abc",
                     "profile_hash": "0" * 12, "time_s": 1.5},
                extra={"radiation": {"theta": [0.0, 1.0]}})
    base.update(over)
    return model.Record(**base)


def test_record_meta_roundtrip_including_none_response_and_extra():
    for rec in (_record(), _record(response=None, status=model.STATUS_ERROR, score=None, measure={})):
        meta = rec.meta()
        assert "bits" not in meta and "response" not in meta, "meta 是純 JSON：陣列另外存"
        assert meta["extra"] == rec.extra and meta["run"]["worker_ver"] == "emforge=abc"
        back = model.Record.from_meta(meta, rec.bits, rec.response)
        for f in dataclasses.fields(model.Record):
            if f.name in ("bits", "response"):
                continue
            assert getattr(back, f.name) == getattr(rec, f.name), f.name
        assert np.array_equal(back.bits, rec.bits)
        assert (back.response is None and rec.response is None) or np.array_equal(back.response, rec.response)


def test_record_constants_are_lowercase_snake():
    assert model.STATUSES == ("queued", "running", "done", "error")
    assert model.KINDS == ("sample", "repeat")
    assert model.ARM_BLIND == "blind"


# ── Job ─────────────────────────────────────────────────────────────────────
def test_job_roundtrip_preserves_unknown_fields():
    """`origin`/`keep_work` 是給之後 `deliver` 留的保留欄位；不認得的鍵要原樣帶著走，不能被 dataclass 吃掉。"""
    d = {"store": "s", "sim_profile": "p", "profile_hash": "h" * 12, "prio": 3, "n": 60, "machine": None,
         "origin": "runtime", "by": "", "at": "2026-09-01T00:00:00", "keep_work": True, "future_key": [1]}
    j = model.Job.from_dict(d)
    assert j.prio == 3 and j.n == 60 and j.extra == {"keep_work": True, "future_key": [1]}
    assert j.to_dict() == d


def test_profile_proposal_job_have_no_keep_project_field():
    """D10／I-1：keep_project 放進任何契約欄位＝策略能開它＝重演磁碟塞爆。"""
    for cls in (model.Profile, model.Proposal, model.Job, model.Record, model.Spec):
        names = {f.name for f in dataclasses.fields(cls)}
        assert not any("keep" in n for n in names), cls.__name__
