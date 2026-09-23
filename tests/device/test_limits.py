# -*- coding: utf-8 -*-
"""tests/device/test_limits.py — `emforge/device/limits.py`：MHS 第 2–4 層——硬限制、前置檢查（重用 gate＋doctor）；confirm token 在 test_instrument。"""
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


def test_limits_json_rejects_wrong_types_naming_the_file_and_field(root):
    """檢查 #16：`{"allowed_profiles": "dual_p01_db075"}`（少一對中括號）以前逐字元變 tuple → 所有 profile 都不准；
    `{"max_sample_s": "1800"}` 以前開機時 TypeError 被當機器卡住重試三次判死整批。現在跟壞 JSON 一樣指名檔案與欄位拒起。"""
    import pytest
    from emforge import paths
    p = paths.limits_json(root)
    for text, field in (('{"allowed_profiles": "dual_p01_db075"}', "allowed_profiles"),
                        ('{"max_sample_s": "1800"}', "max_sample_s"),
                        ('{"min_free_gb": -1}', "min_free_gb"),
                        ('{"confirm_window_s": true}', "confirm_window_s"),
                        ('{"allowed_profiles": [1, 2]}', "allowed_profiles")):
        p.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError, match="limits.json") as ei:
            L.load_limits(root)
        assert field in str(ei.value), text
    with pytest.raises(ValueError, match="allowed_profiles"):
        L.Limits(allowed_profiles="x")


def test_load_limits_accepts_utf8_bom(root):
    """回歸 I-33（2026-09-23）：PowerShell 5 的 Set-Content -Encoding utf8 一定寫 BOM，limits.json 以前讀不起來（Unexpected UTF-8 BOM）。"""
    import json
    from emforge import paths
    p = paths.limits_json(root)
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps(L.Limits().to_dict()).encode("utf-8"))
    lim, src = L.load_limits(root)
    assert lim == L.Limits() and src == str(p)
