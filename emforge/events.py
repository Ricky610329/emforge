"""emforge/events.py — 事件白名單與 `emit`。

事件是 runtime／worker 對外（人、AI 層、report）唯一的敘事管道，所以名字與必填欄位是**封閉集合**：
新事件要加在這裡（連帶 docs/implementation.md 的事件表），不能在別處自由發明。
runtime 寫 `runtime_state/<profile>/events.jsonl`；worker 寫 `queue/log/<tag>.jsonl`（各自單寫者，fs.append_jsonl 契約）。
"""
from . import fs

EVENTS = {
    # ── runtime ──
    "runtime_start": ("runtime_ver", "profile_hash", "pid", "machine"),
    "runtime_stop": ("reason",),
    "reconcile_mismatch": ("detail",),
    "config_reloaded": ("n_strategies",),
    "config_invalid": ("error",),
    "fleet_quiet": ("quiet_s",),
    "profile_paused": ("error_rate", "store"),
    "profile_resumed": ("by",),
    # ── strategy ──
    "strategy_loaded": ("name",),
    "strategy_rejected": ("name", "reason"),
    "strategy_error": ("name", "tick", "error", "consecutive"),
    "strategy_timeout": ("name", "tick", "timeout_s"),
    "strategy_paused": ("name", "after_errors"),
    "strategy_resumed": ("name", "by"),
    "strategy_empty": ("name", "tick"),
    "proposals_validated": ("name", "tick", "n_in", "n_dup", "n_out"),
    # ── dispatch / collect ──
    "batch_dispatched": ("store", "strategy", "n", "prio", "tick", "seed", "kind"),
    "record_added": ("id", "store", "score", "kind"),
    "batch_done": ("store", "n_done", "n_error"),
    "batch_failed": ("store", "reason"),
    "batch_requeued": ("store", "by"),
    "batch_abandoned": ("store", "by"),
    "profile_tamper": ("store", "expected", "got"),
    "index_repaired": ("n",),
    # ── notarize ──
    "record_candidate": ("id", "score", "prev_best"),
    "notarize_dispatched": ("id", "stores"),
    "notarize_pass": ("id", "scores", "conservative", "spread"),
    "notarize_reject": ("id", "scores", "spread", "noise_floor"),
    # ── ledger ──
    "promoted": ("id", "spec", "by"),
    "retired": ("profile", "by"),
    "rescored": ("spec", "n", "by"),
    # ── worker（queue/log/<tag>.jsonl） ──
    "worker_start": ("worker_ver", "machine"),
    "job_claimed": ("store", "prio"),
    "sample_done": ("store", "id", "time_s"),
    "sample_error": ("store", "id", "error", "attempts"),
    "job_done": ("store", "n_done", "n_error"),
    "job_failed": ("store", "reason"),
    "job_yield": ("store", "reason"),
    "gate_rejected": ("store", "reason"),
    "sim_restart": ("store", "reason"),
    "worker_stop": ("reason",),
}


class UnknownEvent(ValueError):
    pass


class EventFieldsMissing(ValueError):
    pass


def make(event: str, /, **fields) -> dict:
    """驗名字與必填欄位，補 `at`／`event`。多給的欄位保留（診斷用）。
    事件名是 positional-only：欄位裡可以有 `name`（策略事件都有），不會撞。"""
    if event not in EVENTS:
        raise UnknownEvent(f"事件 {event!r} 不在白名單（emforge/events.py）")
    missing = [f for f in EVENTS[event] if f not in fields]
    if missing:
        raise EventFieldsMissing(f"事件 {event} 缺必填欄位 {missing}")
    return {"at": fs.now_iso(), "event": event, **fields}


def emit(path, event: str, /, **fields) -> dict:
    """append 一行到 jsonl（單寫者檔）並回傳寫入的 dict。"""
    e = make(event, **fields)
    fs.append_jsonl(path, e)
    return e
