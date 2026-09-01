"""emforge/paths.py — 磁碟命名與名字規則的**唯一真相源**。

任何會落到磁碟的檔名／目錄名都只能從這裡拿；任何會出現在設定檔的名字（profile／spec／策略／store）
都用這裡的規則驗。人讀版在 docs/naming.md；快照釘在 tests/test_paths.py。

#? 為什麼要有這個檔：舊系統的 `<store>_input` 在 8 處手拼字串、佇列狀態檔的副檔名散在 worker 各處，
#  改一個名字要 grep 全 repo。這裡集中之後，改名＝改一個函式＋一條快照測試。

佈局（<root> = EMFORGE_ROOT）：
  registry.py  strategies/<name>.py
  db/<profile>/<id>-<store>.npz  _index.jsonl  RETIRED
  ledger/<profile>/<spec>.json
  queue/jobs.json  jobs.lock  state/<store>.{claim,done,fail}  STOP  STOP.<tag>  log/<tag>.jsonl
  batches/<store>/manifest.json  patterns.npz  results/<id>.json
  runtime_state/<profile>/lock  strategies.yaml  state.json  status.json  events.jsonl  pending.jsonl  STOP
                          inflight/<store>.json  strategies/<name>/
"""
import re
from pathlib import Path

# ── 名字規則 ────────────────────────────────────────────────────────────────
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
#? 保留字（不准當使用者策略名）：`repeat` 是公證的保留 kind、`notarize`／`runtime`／`cli` 是 runtime／CLI 自己產出的
#  store 會填的策略欄位值。`blind` **不在此列**——它是內建策略名兼保留 arm（零演算法對照臂）。
RESERVED_STRATEGY_NAMES = frozenset({"repeat", "notarize", "runtime", "cli"})
COMPATIBLE_ANY = "*"


def is_valid_name(name: str) -> bool:
    """profile／spec／策略／機器 tag 等名字：小寫開頭、小寫字母數字底線。`-` 留給 store 名當欄位分隔。"""
    return bool(NAME_RE.match(name or ""))


# ── 頂層 ────────────────────────────────────────────────────────────────────
def registry_py(root) -> Path:
    return Path(root) / "registry.py"


def user_strategies_dir(root) -> Path:
    return Path(root) / "strategies"


# ── 資料庫 ──────────────────────────────────────────────────────────────────
def db_dir(root, profile: str) -> Path:
    return Path(root) / "db" / profile


def record_file(root, profile: str, rec_id: str, store: str) -> Path:
    """一筆一檔；同 id 不同 store（公證重測）是不同檔。"""
    return db_dir(root, profile) / f"{rec_id}-{store}.npz"


def db_index(root, profile: str) -> Path:
    return db_dir(root, profile) / "_index.jsonl"


def retired_marker(root, profile: str) -> Path:
    return db_dir(root, profile) / "RETIRED"


# ── 榜 ──────────────────────────────────────────────────────────────────────
def ledger_file(root, profile: str, spec: str) -> Path:
    return Path(root) / "ledger" / profile / f"{spec}.json"


# ── 佇列 ────────────────────────────────────────────────────────────────────
def queue_dir(root) -> Path:
    return Path(root) / "queue"


def jobs_file(root) -> Path:
    return queue_dir(root) / "jobs.json"


def jobs_lock(root) -> Path:
    return queue_dir(root) / "jobs.lock"


def queue_state_dir(root) -> Path:
    return queue_dir(root) / "state"


def claim_file(root, store: str) -> Path:
    return queue_state_dir(root) / f"{store}.claim"


def done_file(root, store: str) -> Path:
    return queue_state_dir(root) / f"{store}.done"


def fail_file(root, store: str) -> Path:
    return queue_state_dir(root) / f"{store}.fail"


def queue_stop(root, tag: str | None = None) -> Path:
    """`STOP` 全機收工；`STOP.<tag>` 只停一台。"""
    return queue_dir(root) / ("STOP" if tag is None else f"STOP.{tag}")


def worker_log(root, tag: str) -> Path:
    return queue_dir(root) / "log" / f"{tag}.jsonl"


# ── 批次 ────────────────────────────────────────────────────────────────────
def batch_dir(root, store: str) -> Path:
    return Path(root) / "batches" / store


def batch_manifest(root, store: str) -> Path:
    return batch_dir(root, store) / "manifest.json"


def batch_patterns(root, store: str) -> Path:
    return batch_dir(root, store) / "patterns.npz"


def batch_results_dir(root, store: str) -> Path:
    return batch_dir(root, store) / "results"


def batch_result(root, store: str, rec_id: str) -> Path:
    """逐筆結果檔＝合併語義（I-15）：兩台先後寫同一批也不會整份覆蓋。"""
    return batch_results_dir(root, store) / f"{rec_id}.json"


# ── runtime ─────────────────────────────────────────────────────────────────
def runtime_dir(root, profile: str) -> Path:
    return Path(root) / "runtime_state" / profile


def runtime_lock(root, profile: str) -> Path:
    return runtime_dir(root, profile) / "lock"


def strategies_yaml(root, profile: str) -> Path:
    return runtime_dir(root, profile) / "strategies.yaml"


def state_json(root, profile: str) -> Path:
    return runtime_dir(root, profile) / "state.json"


def status_json(root, profile: str) -> Path:
    return runtime_dir(root, profile) / "status.json"


def events_jsonl(root, profile: str) -> Path:
    return runtime_dir(root, profile) / "events.jsonl"


def pending_jsonl(root, profile: str) -> Path:
    return runtime_dir(root, profile) / "pending.jsonl"


def runtime_stop(root, profile: str) -> Path:
    return runtime_dir(root, profile) / "STOP"


def control_json(root, profile: str) -> Path:
    """CLI → runtime 的單向控制檔（resume 等）；runtime 每 tick 開頭消費並刪除。"""
    return runtime_dir(root, profile) / "control.json"


def inflight_dir(root, profile: str) -> Path:
    return runtime_dir(root, profile) / "inflight"


def inflight_file(root, profile: str, store: str) -> Path:
    return inflight_dir(root, profile) / f"{store}.json"


def strategy_workdir(root, profile: str, strategy: str) -> Path:
    """策略私有持久目錄；runtime 永不讀它。"""
    return runtime_dir(root, profile) / "strategies" / strategy


# ── store 名 ────────────────────────────────────────────────────────────────
def store_name(profile: str, strategy: str, tick: int) -> str:
    """`<profile>-<strategy>-t<tick:05d>`；`-` 是欄位分隔，欄位內只有 snake。"""
    return f"{profile}-{strategy}-t{tick:05d}"


def notarize_store_name(profile: str, tick: int, rec_id: str, n: int) -> str:
    """公證重測：`<profile>-notarize-t<tick:05d>-<id[:8]>-r<n>`。"""
    return f"{profile}-notarize-t{tick:05d}-{rec_id[:8]}-r{n}"


def smoke_store_name(profile: str, rec_id: str, n: int, stamp: str) -> str:
    """人下的 smoke 重測：`<profile>-smoke-<id[:8]>-<YYYYmmddHHMMSS>-r<n>`（沒有 tick，用時間戳）。"""
    return f"{profile}-smoke-{rec_id[:8]}-{stamp}-r{n}"


# ── 整體 ────────────────────────────────────────────────────────────────────
def layout_dirs(root) -> tuple:
    """`emforge init` 要建的目錄（不含 per-profile 子目錄，那些在第一次用到時才建）。"""
    r = Path(root)
    return (
        r / "db", r / "ledger",
        queue_dir(r), queue_state_dir(r), queue_dir(r) / "log",
        r / "batches", r / "runtime_state", user_strategies_dir(r),
    )


def snapshot(root, *, profile: str, store: str, rec_id: str, spec: str, tag: str, strategy: str) -> dict:
    """所有路徑函式的一次性展開——tests/test_paths.py 用它釘快照。"""
    return {
        "registry_py": registry_py(root),
        "user_strategies_dir": user_strategies_dir(root),
        "db_dir": db_dir(root, profile),
        "record_file": record_file(root, profile, rec_id, store),
        "db_index": db_index(root, profile),
        "retired_marker": retired_marker(root, profile),
        "ledger_file": ledger_file(root, profile, spec),
        "jobs_file": jobs_file(root),
        "jobs_lock": jobs_lock(root),
        "claim_file": claim_file(root, store),
        "done_file": done_file(root, store),
        "fail_file": fail_file(root, store),
        "queue_stop": queue_stop(root),
        "queue_stop_tag": queue_stop(root, tag),
        "worker_log": worker_log(root, tag),
        "batch_manifest": batch_manifest(root, store),
        "batch_patterns": batch_patterns(root, store),
        "batch_results_dir": batch_results_dir(root, store),
        "batch_result": batch_result(root, store, rec_id),
        "runtime_lock": runtime_lock(root, profile),
        "strategies_yaml": strategies_yaml(root, profile),
        "state_json": state_json(root, profile),
        "status_json": status_json(root, profile),
        "events_jsonl": events_jsonl(root, profile),
        "pending_jsonl": pending_jsonl(root, profile),
        "runtime_stop": runtime_stop(root, profile),
        "control_json": control_json(root, profile),
        "inflight_file": inflight_file(root, profile, store),
        "strategy_workdir": strategy_workdir(root, profile, strategy),
    }
