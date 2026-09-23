"""emforge/events.py — 事件白名單與 `emit`。

事件是 runtime／worker 對外（人、AI 層、report）唯一的敘事管道，所以名字與必填欄位是**封閉集合**：
新事件要加在這裡（連帶 docs/implementation.md 的事件表），不能在別處自由發明。
runtime 寫 `runtime_state/<profile>/events.jsonl`；worker 寫 `queue/log/<tag>.jsonl`（各自單寫者，`Depot.append` 契約）。
"""
from .depot import open_depot
from .model import now_iso

EVENTS = {
    # ── runtime ──
    "runtime_start": ("runtime_ver", "profile_hash", "pid", "machine"),
    "runtime_stop": ("reason",),
    "lock_lost": ("owner",),
    "tick_error": ("tick", "error", "consecutive"),        # 檢查 #7：tick 裡的例外記下來、續跑
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
    "inbox_received": ("name", "sid", "n"),
    "inbox_rejected": ("name", "sid", "reason"),
    "batch_dispatched": ("store", "strategy", "n", "prio", "tick", "seed", "kind"),
    "dispatch_failed": ("name", "tick", "error"),
    "record_added": ("id", "store", "score", "kind"),
    "batch_done": ("store", "n_done", "n_error"),
    "batch_failed": ("store", "reason"),
    "batch_requeued": ("store", "by"),
    "batch_abandoned": ("store", "by"),
    "profile_tamper": ("store", "expected", "got"),
    "stray_result": ("store", "id"),                     # I-19：結果目錄裡不在 manifest 的檔——點名一次、不入庫、不再重讀
    "collect_error": ("store", "error"),                 # I-19：單一 store 收結果炸了——其他 store 照收，下 tick 再試
    "dispatch_recovered": ("store",),                    # I-20：派工半途失敗的意圖在執行中補完（不必重啟）
    "index_repaired": ("n",),
    "db_unreadable": ("profile", "n", "stems"),          # 檢查 #4：起動時跳過的壞／缺紀錄
    # ── notarize ──
    "record_candidate": ("id", "score", "prev_best"),
    "notarize_dispatched": ("id", "stores"),
    "notarize_pass": ("id", "scores", "conservative", "spread"),
    "notarize_reject": ("id", "scores", "spread", "noise_floor"),
    # ── ledger ──
    "promoted": ("id", "spec", "by"),
    "retired": ("profile", "by"),
    "rescored": ("spec", "n", "by"),
    "ledger_tamper": ("spec", "detail"),
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
    "worker_error": ("error", "consecutive"),          # 檢查 #7：主迴圈一圈裡的例外（pick／log／release）
    "outbox_held": ("store", "id", "reason"),          # I-35：待傳結果回填不了（不相容／批次不存在／已 abandon）→ 搬到 results_held/
    "worker_stop": ("reason",),
    # ── 儀器（devices/<tag>/log.jsonl；單寫者＝該台 Instrument） ──
    "device_start": ("tag", "worker_ver", "pid"),
    "device_stop": ("tag", "reason"),
    "device_fault": ("tag", "error"),
    "device_simulate": ("tag", "id", "by", "status", "time_s"),
    "device_abort": ("tag", "by"),
    "lease_refused": ("tag", "owner", "holder"),
    "estop_engaged": ("tag", "scope", "by", "reason"),
    "estop_cleared": ("tag", "scope"),
    "device_serve": ("tag", "url", "host", "port", "auth"),          # M15：MCP server 起來了
    "device_stop_worker": ("tag", "by"),                              # M15：經 MCP 建／刪 STOP.<tag>
    "device_resume_worker": ("tag", "by"),
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
    return {"at": now_iso(), "event": event, **fields}


def emit(depot, key: str, event: str, /, **fields) -> dict:
    """append 一筆到日誌 key（單寫者）並回傳寫入的 dict。`depot` 吃 `Depot | str | Path`。
    三個參數都是 positional-only：事件欄位裡有 `name`／`key`，不能撞。"""
    e = make(event, **fields)
    open_depot(depot).append(key, e)
    return e
