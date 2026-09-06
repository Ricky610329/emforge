# -*- coding: utf-8 -*-
"""tests/device/test_limits.py — `emforge/device/limits.py`：MHS 第 2–4 層——硬限制、前置檢查（重用 gate＋doctor）、兩段式 confirm。"""
from emforge import doctor, profiles, testing
from emforge.depot import MemoryDepot
from emforge.device import limits as L

P = testing.FAKE_PROFILE


def test_limits_roundtrip_dict_and_defaults():
    lim = L.Limits()
    assert lim.allowed_profiles == () and lim.max_sample_s >= 3600 and lim.min_free_gb == doctor.MIN_FREE_GB
    d = lim.to_dict()
    assert L.Limits.from_dict(d) == lim and L.Limits.from_dict({**d, "allowed_profiles": ["a"]}).allowed_profiles == ("a",)


def test_preconditions_reject_not_allowed_retired_timeout_and_blocking_health(root, monkeypatch):
    d = MemoryDepot()
    testing.make_fake_root(root, depot=d)
    monkeypatch.setattr(doctor, "ansysedt_running", lambda: False)
    assert L.check_preconditions(P, limits=L.Limits(), depot=d, root=root) == []
    assert any("allowed" in p for p in L.check_preconditions(P, limits=L.Limits(allowed_profiles=("other",)), depot=d, root=root))
    assert any("timeout" in p for p in L.check_preconditions(P, limits=L.Limits(max_sample_s=1.0), depot=d, root=root))
    profiles.retire(d, P.name, by="t")
    assert any("retired" in p for p in L.check_preconditions(P, limits=L.Limits(), depot=d, root=root))
    monkeypatch.setattr(doctor, "ansysedt_running", lambda: True)
    h = doctor.health(root, depot=d)
    assert any("ansysedt" in p for p in L.check_preconditions(P, limits=L.Limits(), depot=d, root=root, health=h))


def test_confirm_token_is_stable_within_window_and_single_use():
    t0 = 1_700_000_000.0
    tok = L.confirm_token("s3cret", "simulate", "fake_f1/abc", now=t0)
    assert len(tok) == 8 and tok == L.confirm_token("s3cret", "simulate", "fake_f1/abc", now=t0 + 100)
    assert tok != L.confirm_token("s3cret", "abort", "fake_f1/abc", now=t0)
    assert tok != L.confirm_token("other", "simulate", "fake_f1/abc", now=t0)
    used = set()
    assert L.confirm_ok("s3cret", "simulate", "fake_f1/abc", tok, used=used, now=t0 + 100) is True
    assert L.confirm_ok("s3cret", "simulate", "fake_f1/abc", tok, used=used, now=t0 + 100) is False, "單次使用"
    assert L.confirm_ok("s3cret", "simulate", "fake_f1/abc", "00000000", used=set(), now=t0) is False
    prev = L.confirm_token("s3cret", "simulate", "fake_f1/abc", now=t0 - 600)
    assert L.confirm_ok("s3cret", "simulate", "fake_f1/abc", prev, used=set(), now=t0) is True, "上一窗還收（窗邊界）"
    old = L.confirm_token("s3cret", "simulate", "fake_f1/abc", now=t0 - 1200)
    assert L.confirm_ok("s3cret", "simulate", "fake_f1/abc", old, used=set(), now=t0) is False, "兩窗前過期"
