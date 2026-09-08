"""候選優先級、同級公平與背景填空；正在量測的單筆不搶占。"""
from dataclasses import replace

from .. import priority, strategy
from ..model import KIND_SAMPLE, sha1_hex
from .dispatch import dispatch
from . import inbox


def strategy_seed(base: int, profile: str, name: str, tick: int) -> int:
    return int(sha1_hex(f"{base}:{profile}:{name}:{tick}".encode("utf-8"))[:8], 16)


def _entries(rt):
    entries = []
    for sc in rt.config.strategies:
        if not sc.enabled or rt.strategy_state(sc.name)["paused"]:
            continue
        plans = inbox.plans(rt, sc) if sc.kind == "inbox" else []
        if sc.kind == "inbox" and not plans:
            continue
        plan = plans[0] if plans else None
        entries.append((plan["prio"] if plan else sc.prio, sc, plan))
    return sorted(entries, key=lambda x: (x[0], (rt.strategy_state(x[1].name)["last_dispatch_tick"] or 0)))


def schedule(rt) -> None:
    if rt.state.get("paused_profile"):
        return
    tick = rt.state["tick"]
    for prio, sc, plan in _entries(rt):
        mine = [i for i in rt.inflight() if i["strategy"] == sc.name and i["kind"] == KIND_SAMPLE]
        jobs = {j.store: j for j in rt.queue.list()}
        # 低優先工作不占用較高優先候選的配額；同級與更高級仍受 max_inflight 約束。
        active = [i for i in mine if (jobs[i["store"]].prio if i["store"] in jobs else i["prio"]) <= prio]
        if len(active) >= sc.max_inflight:
            continue
        if prio >= rt.config.runtime.background_prio and not _background_allowed(rt):
            continue
        if plan:
            inbox.take(rt, sc, plan=plan)
            continue
        seed = sc.seed if sc.seed is not None else strategy_seed(rt.state["seed_base"], rt.profile.name, sc.name, tick)
        limited = replace(sc, batch=1) if prio >= rt.config.runtime.background_prio else sc
        props = _propose(rt, limited, seed, tick)
        if props is None:
            continue
        rt.strategy_state(sc.name)["errors_consecutive"] = 0
        if not props:
            rt.event("strategy_empty", name=sc.name, tick=tick)
            continue
        try:
            if _dispatch_props(rt, sc, props, seed, tick):
                rt.strategy_state(sc.name)["last_dispatch_tick"] = tick
        except Exception as e:  # 派工錯不是策略錯，runtime 下輪可恢復已保存的意圖。
            rt.event("dispatch_failed", name=sc.name, tick=tick, error=f"{type(e).__name__}: {e}")


def _dispatch_props(rt, sc, props, seed, tick):
    if not any(p.priority is not None for p in props):
        return dispatch(rt, sc.name, props, tick=tick, seed=seed, prio=sc.prio)
    stores = []
    for index, prop in sorted(enumerate(props), key=lambda x: priority.value(x[1].priority, sc.prio)):
        store = dispatch(rt, sc.name, [prop], tick=tick, seed=seed,
                         prio=priority.value(prop.priority, sc.prio), part=index)
        if store:
            stores.append(store)
    return stores


def _background_allowed(rt):
    # 已在跑的前景不擋空閒機器；同 profile 最多預備一個尚未認領的背景 job。
    return not any(j.sim_profile == rt.profile_name and rt.queue.state(j.store) == "queued"
                   for j in rt.queue.list())


def _propose(rt, sc, seed: int, tick: int):
    timeout_s = sc.propose_timeout_s or rt.config.runtime.propose_timeout_s
    try:
        return rt._propose(rt.root, rt.profile, sc.name, budget=sc.batch, seed=seed, tick=tick, params=sc.params,
                           timeout_s=timeout_s, depot=rt.depot)
    except strategy.StrategyTimeout as e:
        rt.event("strategy_timeout", name=sc.name, tick=tick, timeout_s=timeout_s)
        _failed(rt, sc, f"StrategyTimeout: {e}")
    except Exception as e:
        _failed(rt, sc, f"{type(e).__name__}: {e}")
    return None


def _failed(rt, sc, msg: str) -> None:
    st = rt.strategy_state(sc.name)
    st["errors_consecutive"] += 1
    rt.event("strategy_error", name=sc.name, tick=rt.state["tick"], error=msg, consecutive=st["errors_consecutive"])
    if st["errors_consecutive"] >= rt.config.runtime.strategy_error_limit:
        st["paused"] = True
        rt.event("strategy_paused", name=sc.name, after_errors=st["errors_consecutive"])
