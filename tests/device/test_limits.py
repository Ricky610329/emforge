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


def test_confirm_valid_checks_without_consuming():
    """M15：驗證與消費分開——操作真的開始才記 used；被拒（busy／急停）不燒 token。"""
    t0 = 1_700_000_000.0
    tok = L.confirm_token("s3cret", "simulate", "k", now=t0)
    used = set()
    assert L.confirm_valid("s3cret", "simulate", "k", tok, used=used, now=t0) is True and used == set()
    assert L.confirm_valid("s3cret", "simulate", "k", "00000000", used=used, now=t0) is False
    used.add(tok)
    assert L.confirm_valid("s3cret", "simulate", "k", tok, used=used, now=t0) is False


def test_load_limits_from_root_file_default_when_missing_and_loud_when_broken(root):
    """M16：部署設定 `<root>/limits.json`（本機路徑）；沒有＝預設；壞 JSON 要指名檔案（別默默用預設把上限放掉）。"""
    from emforge import paths
    lim, src = L.load_limits(root)
    assert lim == L.Limits() and src == "default"
    paths.limits_json(root).write_text('{"max_sample_s": 1234, "allowed_profiles": ["fake_f1"], "unknown": 1}', encoding="utf-8")
    lim, src = L.load_limits(root)
    assert lim.max_sample_s == 1234 and lim.allowed_profiles == ("fake_f1",) and src == str(paths.limits_json(root))
    paths.limits_json(root).write_text("{oops", encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="limits.json"):
        L.load_limits(root)
    assert set(L.LIMITS_TEMPLATE) == set(L.Limits().to_dict()), "init 範本欄位＝Limits 欄位"
