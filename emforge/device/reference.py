"""emforge/device/reference.py — 儀器說明檔（MHS 的自動產生自然語言說明）：`devices/<tag>/reference.md`＋`.json`。

給人與 AI 層讀：這台能量哪些 profile（註冊表 ∩ allowed_profiles）、限制、體檢、近期一筆多久、能對它做什麼
（程序清單＝M15 的 MCP tools 名單，一對一）。儀器 open 成功與每小時各刷一次（`Instrument._beat`）；寫失敗不炸。
"""
from .. import doctor, profiles
from .. import paths
from ..model import now_iso

#? 程序清單＝MCP tools（M15）；`mutating` 對應 MCP 的 readOnlyHint 反面。解除急停**不在**清單：只能 CLI。
PROCEDURES = (
    {"name": "device_state", "mutating": False, "description": "狀態字典（state／owner／store／n_done…；offline 由讀者推導）"},
    {"name": "device_describe", "mutating": False, "description": "這份說明檔（markdown 或 json）"},
    {"name": "device_selfcheck", "mutating": False, "description": "depot 探針＋機器體檢＋前置檢查問題清單"},
    {"name": "device_log", "mutating": False, "description": "裝置日誌最後 N 筆"},
    {"name": "device_simulate", "mutating": True, "description": "跑一筆（兩段式：先拿 token 再帶 confirm）；結果不入 db"},
    {"name": "device_abort", "mutating": True, "description": "殺正在跑的那筆（＝kill；吃一次 attempts）"},
    {"name": "device_estop", "mutating": True, "description": "按下這台的急停（解除只能 CLI：emforge device-estop clear --confirm）"},
    {"name": "device_stop_worker", "mutating": True, "description": "建 queue/STOP.<tag>：worker 跑完當前 job 收工"},
    {"name": "device_resume_worker", "mutating": True, "description": "刪 queue/STOP.<tag>"},
)


def _profile_row(p) -> dict:
    return {"name": p.name, "profile_hash": p.profile_hash, "simulator": p.simulator, "geom_ver": p.geom_ver,
            "shape": list(p.shape), "labels": list(p.labels), "n_points": p.n_points, "timeout_s": p.timeout_s,
            "measure": p.measure, "spec": p.spec, "retired": bool(p.retired)}


def render(inst) -> tuple:
    """(markdown, dict)。不碰模擬器；體檢會跑 tasklist（~0.1 s），所以只在 open 成功與每小時刷。"""
    allowed = inst.limits.allowed_profiles
    prof = [_profile_row(p) for p in profiles.all_profiles() if not allowed or p.name in allowed]
    d = {"tag": inst.tag, "worker_ver": inst.state.worker_ver, "state": inst.state.state, "pid": inst.state.pid,
         "url": inst.state.url, "generated_at": now_iso(), "profiles": prof, "limits": inst.limits.to_dict(),
         "limits_source": inst.limits_source,
         "health": doctor.health(inst.root, depot=inst.depot), "median_time_s": inst.median_time_s(),
         "estop": inst.estop_engaged(), "procedures": [dict(p) for p in PROCEDURES],
         "keys": {"state": paths.device_state(inst.tag), "log": paths.device_log(inst.tag),
                  "adhoc": paths.adhoc_dir(inst.tag), "estop_device": paths.estop_device(inst.tag),
                  "estop_fleet": paths.estop_fleet()}}
    return _markdown(d), d


def _markdown(d: dict) -> str:
    h = d["health"]
    med = d["median_time_s"]
    es = d["estop"]
    lines = [f"# 儀器 {d['tag']}", "",
             f"- 產生時間：{d['generated_at']}；worker_ver `{d['worker_ver']}`；pid {d['pid']}；狀態 `{d['state']}`"
             + (f"；MCP `{d['url']}`" if d.get("url") else ""),
             "- 近期一筆中位數：" + ("尚無" if med is None else f"{med:.0f} s"),
             "- 急停：" + (f"**{es['scope']}** {es.get('reason')}" if es else "無"),
             "", "## 可量的 profile（註冊表 ∩ allowed_profiles）", "",
             "| profile | profile_hash | geom_ver | shape | labels | n_points | timeout_s | 退役 |", "|---|---|---|---|---|---|---|---|"]
    for p in d["profiles"]:
        lines.append(f"| `{p['name']}` | `{p['profile_hash']}` | {p['geom_ver']} | {p['shape']} | {p['labels']} | "
                     f"{p['n_points']} | {p['timeout_s']} | {'是' if p['retired'] else ''} |")
    lines += ["", "## 限制（Limits）", "", f"- 來源：`{d.get('limits_source', 'default')}`（`<root>/limits.json`；沒有＝預設）"]
    lines += [f"- `{k}`：{v}" for k, v in d["limits"].items()]
    lines += ["", "## 體檢", "",
              f"- 系統碟剩餘 {h['free_gb']:.1f} GB（工作目錄 {h['work_root']}）；ansysedt 在跑：{h['ansysedt_running']}",
              f"- depot `{h['depot_spec']}`：{'OK' if not h['depot_problems'] else '；'.join(h['depot_problems'])}",
              f"- 阻擋：{'無' if not h['blocking'] else '；'.join(h['blocking'])}",
              "", "## 程序（＝MCP tools；解除急停只能 CLI）", ""]
    lines += [f"- `{p['name']}`{'（會改狀態）' if p['mutating'] else ''}：{p['description']}" for p in d["procedures"]]
    lines += ["", "## 安全", "",
              "1. tool 允許名單＋`allowed_profiles`；2. `Limits` 硬限制；3. 前置檢查＝守門（profile_hash／geom_ver／labels）＋體檢；",
              "4. 兩段式 confirm（token 10 分鐘窗、單次）；5. 急停三層（全機／單機／本機），open／simulate 前硬檢查，正在跑的那筆 abort。",
              "", f"key：state `{d['keys']['state']}`、log `{d['keys']['log']}`、adhoc `{d['keys']['adhoc']}`、"
              f"急停 `{d['keys']['estop_fleet']}`／`{d['keys']['estop_device']}`。", ""]
    return "\n".join(lines)


def write_reference(inst) -> bool:
    """刷說明檔（md＋json）；寫失敗回 False（記 device_fault）。"""
    try:
        md, d = render(inst)
        inst.depot.put_bytes(paths.device_reference_md(inst.tag), md.encode("utf-8"))
        inst.depot.put_json(paths.device_reference_json(inst.tag), d)
        return True
    except Exception as e:  # noqa: BLE001 — 說明檔寫不進去不能殺儀器
        inst.log("device_fault", error=f"write_reference: {type(e).__name__}: {e}")
        return False
