"""emforge/report.py — 每策略報表：n／best／mean／命中率／dup_dropped／P(勝 blind)。

規則（D8）：對照臂不強制，但 blind 樣本 n < k_min 時**只印數字、不印任何比較**——沒有零演算法臂就說不出「較好」。
`dup_dropped` 與 P(勝 blind) 分開印：前者診斷「沒貨」（提案都重複），後者診斷「排不動」。
跨 profile 預設拒絕並排（era 不可比），加旗標才印且帶警告。同一 store 兩種 worker_ver → 警告（I-10）。
"""
import numpy as np

from . import paths, strategy
from .db import Database
from .depot import open_depot
from .model import ARM_BLIND, KIND_REPEAT, STATUS_DONE

DEFAULT_K_MIN = 20
NON_COMPARABLE = {"notarize"}              # runtime 自己產出的重測
NON_COMPARABLE_PREFIXES = ("cli:",)        # 人下的命令（smoke／deliver…）


class CrossProfileRefused(Exception):
    pass


def p_beats_blind(scores, blind_scores, n_boot: int = 1000, seed: int = 0) -> dict | None:
    """P(隨機抽一筆策略樣本 > 隨機抽一筆 blind)，平手算半分；點估計精確（全配對），bootstrap 給 95% 區間。"""
    s, b = np.asarray(list(scores), float), np.asarray(list(blind_scores), float)
    if s.size == 0 or b.size == 0:
        return None

    def prob(x, y):
        return float((x[:, None] > y[None, :]).mean() + 0.5 * (x[:, None] == y[None, :]).mean())

    p = prob(s, b)
    rng = np.random.default_rng(seed)
    boots = [prob(rng.choice(s, s.size), rng.choice(b, b.size)) for _ in range(n_boot)]
    lo, hi = float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))
    return {"p": p, "lo": min(lo, p), "hi": max(hi, p), "n": int(s.size), "n_blind": int(b.size)}


def _comparable(name: str) -> bool:
    return name not in NON_COMPARABLE and not name.startswith(NON_COMPARABLE_PREFIXES)


def _dup_dropped(depot, profile: str) -> dict:
    out: dict = {}
    for e in depot.read_log(paths.events_jsonl(profile)):
        if e.get("event") == "proposals_validated":
            out[e["name"]] = out.get(e["name"], 0) + int(e.get("n_dup", 0))
    return out


def _blind_reference(metas) -> list:
    """blind 參考分佈：arm=blind 的 done 樣本，**排除公證重測**。
    #! 檢查 #3（2026-09-07）：notarize 的重測沿用 arm、kind=repeat——同一片重量三次不是三個獨立樣本，以前灌水 blind_n 過 k_min
    #  閘、把三筆同值塞進分佈頂端，P(勝 blind) 零新增樣本就翻盤（實測 0.52 → 0.47）。"""
    return [m for m in metas if m["status"] == STATUS_DONE and m["score"] is not None
            and m["arm"] == ARM_BLIND and m.get("kind") != KIND_REPEAT]


def blind_count(db: Database, profile: str) -> int:
    return len(_blind_reference(db.metas(profile)))


def strategy_rows(db: Database, profile: str, *, k_min: int = DEFAULT_K_MIN) -> list:
    metas = db.metas(profile)
    done = [m for m in metas if m["status"] == STATUS_DONE and m["score"] is not None]
    blind = [m["score"] for m in _blind_reference(done)]
    dup = _dup_dropped(db.depot, profile)
    conservative: dict = {}
    for m in done:
        conservative[m["id"]] = min(conservative.get(m["id"], m["score"]), m["score"])
    rows = []
    for name in sorted({m["strategy"] for m in metas}):
        mine = [m for m in metas if m["strategy"] == name]
        mine_done = [m for m in mine if m["status"] == STATUS_DONE and m["score"] is not None]
        scores = [m["score"] for m in mine_done]
        best = max((conservative[m["id"]] for m in mine_done), default=None)
        has_non_blind = any(m["arm"] != ARM_BLIND for m in mine_done)
        pb = None
        if _comparable(name) and has_non_blind and scores and len(blind) >= k_min:
            pb = p_beats_blind(scores, blind)["p"]
        rows.append({"strategy": name, "n": len(mine), "n_done": len(mine_done), "best": best,
                     "mean": float(np.mean(scores)) if scores else None,
                     "hit_rate": float(np.mean([s >= 0 for s in scores])) if scores else None,
                     "dup_dropped": dup.get(name, 0), "p_beats_blind": pb, "comparable": _comparable(name)})
    return rows


def worker_ver_warnings(db: Database, profile: str) -> list:
    """回歸 I-10：同一 store 出現兩種 worker_ver＝有人只 pull 沒重啟／中途換代。"""
    by_store: dict = {}
    for m in db.metas(profile):
        v = m.get("worker_ver")
        if v is not None:
            by_store.setdefault(m["store"], set()).add(v)
    return [f"⚠ store {s} 有 {len(v)} 種 worker_ver：{sorted(v)}（只 pull 沒重啟？I-10）"
            for s, v in sorted(by_store.items()) if len(v) > 1]


def _k_min_from_yaml(depot, profile: str) -> int:
    try:
        text = depot.get_bytes(paths.strategies_yaml(profile)).decode("utf-8")
        return strategy.parse_strategies_yaml(text).runtime.k_min
    except Exception:  # noqa: BLE001 — 沒有 yaml（純資料庫瀏覽）就用預設
        return DEFAULT_K_MIN


def _fmt(v) -> str:
    if v is None:
        return "—"
    return f"{v:.2f}" if isinstance(v, float) else str(v)


def _render(profile: str, rows: list, k_min: int, blind_n: int) -> list:
    show_p = blind_n >= k_min
    lines = [f"## {profile}", ""]
    if not show_p:
        lines.append(f"blind 樣本 n={blind_n} < k_min={k_min} → 只印數字，不印比較（D8：沒有零演算法臂就說不出「較好」）")
        lines.append("")
    header = ["策略", "n", "done", "best", "mean", "命中率", "dup_dropped"] + (["P(勝 blind)"] if show_p else [])
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    for r in rows:
        cells = [r["strategy"], r["n"], r["n_done"], r["best"], r["mean"], r["hit_rate"], r["dup_dropped"]]
        if show_p:
            cells.append(r["p_beats_blind"] if r["comparable"] else None)
        lines.append("| " + " | ".join(_fmt(c) for c in cells) + " |")
    lines.append("")
    return lines


def report(depot, profile_names: list, *, cross_profile: bool = False, k_min: int | None = None) -> str:
    if len(profile_names) > 1 and not cross_profile:
        raise CrossProfileRefused("不同 profile 的分數不可比（era ≡ profile）；要並排請加 --cross-profile")
    depot = open_depot(depot)
    db = Database(depot)
    lines = []
    if len(profile_names) > 1:
        lines += ["⚠ 跨 profile 並排：不同儀器的分數不可比，只供瀏覽、不供裁決。", ""]
    for p in profile_names:
        km = k_min if k_min is not None else _k_min_from_yaml(depot, p)
        lines += _render(p, strategy_rows(db, p, k_min=km), km, blind_count(db, p))
        lines += worker_ver_warnings(db, p)
    return "\n".join(lines)
