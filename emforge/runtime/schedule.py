"""emforge/runtime/schedule.py — 排程：靜態 prio、每策略 max_inflight（＝節奏）、背景填空、策略例外→暫停。

- prio 小者先；prio ≥ background_prio 的是背景填空，只在**沒有前景 inflight 且佇列沒有排隊 job** 時跑（D5）。
- max_inflight（預設 1）＝「上批回來才輪到它」，不用 callback（D4）。
- 策略例外／逾時 → 事件、本 tick 跳過、連 strategy_error_limit 次 → 暫停該策略（操作保護，D9）；
  #! 回歸 I-4（2026-08-03）：背景自產例外殺死 worker。這裡任何策略錯都**絕不**終止 runtime。
"""
from .. import strategy
from ..model import KIND_SAMPLE, sha1_hex
from .dispatch import dispatch


def strategy_seed(base: int, profile: str, name: str, tick: int) -> int:
    """可重現：同 (base, profile, 策略, tick) 同 seed；記進 inflight。"""
    return int(sha1_hex(f"{base}:{profile}:{name}:{tick}".encode("utf-8"))[:8], 16)


def schedule(rt) -> None:
    if rt.state.get("paused_profile"):
        return
    cfg, tick = rt.config, rt.state["tick"]
    inflight = rt.inflight()
    for sc in sorted(cfg.strategies, key=lambda s: s.prio):
        st = rt.strategy_state(sc.name)
        if not sc.enabled or st["paused"]:
            continue
        mine = [i for i in inflight if i["strategy"] == sc.name and i["kind"] == KIND_SAMPLE]
        if len(mine) >= sc.max_inflight:
            continue
        if sc.prio >= cfg.runtime.background_prio and not _background_allowed(rt, inflight, cfg.runtime.background_prio):
            continue
        seed = sc.seed if sc.seed is not None else strategy_seed(rt.state["seed_base"], rt.profile.name, sc.name, tick)
        props = _propose(rt, sc, seed, tick)
        if props is None:
            continue
        st["errors_consecutive"] = 0
        if not props:
            rt.event("strategy_empty", name=sc.name, tick=tick)
            continue
        try:
            store = dispatch(rt, sc.name, props, tick=tick, seed=seed, prio=sc.prio)
        except Exception as e:  # noqa: BLE001 — 派工失敗（StoreExists／鎖逾時／NAS）不是策略的錯，也不能殺 runtime（review-3）
            rt.event("dispatch_failed", name=sc.name, tick=tick, error=f"{type(e).__name__}: {e}")
            continue
        if store:
            st["last_dispatch_tick"] = tick
            inflight.append({"store": store, "strategy": sc.name, "kind": KIND_SAMPLE, "prio": sc.prio, "ids": []})


def _background_allowed(rt, inflight: list, background_prio: int) -> bool:
    if any(i["prio"] < background_prio for i in inflight):
        return False
    return not any(rt.queue.state(j.store) == "queued" for j in rt.queue.list())


def _propose(rt, sc, seed: int, tick: int):
    """回提案 list；失敗回 None（已記事件、已計連敗）。"""
    timeout_s = sc.propose_timeout_s or rt.config.runtime.propose_timeout_s
    try:
        return rt._propose(rt.root, rt.profile, sc.name, budget=sc.batch, seed=seed, tick=tick, params=sc.params,
                           timeout_s=timeout_s)
    except strategy.StrategyTimeout as e:
        rt.event("strategy_timeout", name=sc.name, tick=tick, timeout_s=timeout_s)
        _failed(rt, sc, f"StrategyTimeout: {e}")
    except Exception as e:  # noqa: BLE001 — 策略碼是外來的；runtime 永遠活著
        _failed(rt, sc, f"{type(e).__name__}: {e}")
    return None


def _failed(rt, sc, msg: str) -> None:
    st = rt.strategy_state(sc.name)
    st["errors_consecutive"] += 1
    rt.event("strategy_error", name=sc.name, tick=rt.state["tick"], error=msg, consecutive=st["errors_consecutive"])
    if st["errors_consecutive"] >= rt.config.runtime.strategy_error_limit:
        st["paused"] = True
        rt.event("strategy_paused", name=sc.name, after_errors=st["errors_consecutive"])
