"""emforge/paths.py — 磁碟命名與名字規則的**唯一真相源**。

任何會落到磁碟的檔名／目錄名都只能從這裡拿；任何會出現在設定檔的名字（profile／spec／策略／store）
都用這裡的規則驗。人讀版在 docs/naming.md；快照釘在 tests/test_paths.py。

#? 為什麼要有這個檔：舊系統的 `<store>_input` 在 8 處手拼字串、佇列狀態檔的副檔名散在 worker 各處，
#  改一個名字要 grep 全 repo。這裡集中之後，改名＝改一個函式＋一條快照測試。

**兩種回傳值，別搞混**：
- 共享協調狀態（跨機器、要換得掉後端）＝ `Depot` 的 **key**：POSIX 相對字串，目錄前綴帶尾 `/`。
  這些函式**不吃 root**——root 是 depot 的事，不是命名的事。
- 本機路徑（程式碼載入、策略私有工作目錄）＝ `pathlib.Path`，仍吃 `root`；這些東西不抽象（見檔尾一節）。

佈局（key；<root> 由 depot 決定）：
  registry.py  strategies/<name>.py                      ← 本機路徑
  db/<profile>/<id>-<store>.npz  _index.jsonl  _imported.json  RETIRED
  ledger/<profile>/<spec>.json
  queue/jobs.json  jobs.lock  state/<store>.{claim,done,fail}  STOP  STOP.<tag>  log/<tag>.jsonl
  batches/<store>/manifest.json  patterns.npz  results/<id>.json
  runtime_state/<profile>/lock  strategies.yaml  state.json  status.json  events.jsonl  pending.jsonl  STOP
                          control.json  inflight/<store>.json  strategies/<name>/   ← 最後這個是本機路徑
"""
import re
from pathlib import Path

# ── 名字規則 ────────────────────────────────────────────────────────────────
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
#? 保留字（不准當使用者策略名）：`repeat` 是公證的保留 kind、`notarize`／`runtime`／`cli` 是 runtime／CLI 自己產出的
#  store 會填的策略欄位值。`blind` **不在此列**——它是內建策略名兼保留 arm（零演算法對照臂）。
RESERVED_STRATEGY_NAMES = frozenset({"repeat", "notarize", "runtime", "cli"})
COMPATIBLE_ANY = "*"

#? 頂層前綴：`ensure_prefixes` 與列舉用；下面每個 key 都由它們拼出來，改名只改這五行。
DB = "db/"
LEDGER = "ledger/"
QUEUE = "queue/"
BATCHES = "batches/"
RUNTIME_STATE = "runtime_state/"

_RECORD_EXT = ".npz"
_RESULT_EXT = ".json"


def is_valid_name(name: str) -> bool:
    """profile／spec／策略／機器 tag 等名字：小寫開頭、小寫字母數字底線。`-` 留給 store 名當欄位分隔。"""
    return bool(NAME_RE.match(name or ""))


# ── 資料庫 ──────────────────────────────────────────────────────────────────
def db_dir(profile: str) -> str:
    return f"{DB}{profile}/"


def record_stem(rec_id: str, store: str) -> str:
    """紀錄檔主幹（不含副檔名）＝索引行的主鍵。"""
    return f"{rec_id}-{store}"


def record_by_stem(profile: str, stem: str) -> str:
    return f"{db_dir(profile)}{stem}{_RECORD_EXT}"


def record_file(profile: str, rec_id: str, store: str) -> str:
    """一筆一檔；同 id 不同 store（公證重測）是不同檔。"""
    return record_by_stem(profile, record_stem(rec_id, store))


def db_index(profile: str) -> str:
    return f"{db_dir(profile)}_index.jsonl"


def db_imported(profile: str) -> str:
    """匯入器的 store 簽名帳（`import-legacy` 重跑跳過未變的 store）。"""
    return f"{db_dir(profile)}_imported.json"


def retired_marker(profile: str) -> str:
    return f"{db_dir(profile)}RETIRED"


# ── 榜 ──────────────────────────────────────────────────────────────────────
def ledger_file(profile: str, spec: str) -> str:
    return f"{LEDGER}{profile}/{spec}.json"


# ── 佇列 ────────────────────────────────────────────────────────────────────
def queue_dir() -> str:
    return QUEUE


def jobs_file() -> str:
    return f"{QUEUE}jobs.json"


def jobs_lock() -> str:
    return f"{QUEUE}jobs.lock"


def queue_state_dir() -> str:
    return f"{QUEUE}state/"


def queue_log_dir() -> str:
    return f"{QUEUE}log/"


def claim_file(store: str) -> str:
    return f"{queue_state_dir()}{store}.claim"


def done_file(store: str) -> str:
    return f"{queue_state_dir()}{store}.done"


def fail_file(store: str) -> str:
    return f"{queue_state_dir()}{store}.fail"


def queue_stop(tag: str | None = None) -> str:
    """`STOP` 全機收工；`STOP.<tag>` 只停一台。"""
    return QUEUE + ("STOP" if tag is None else f"STOP.{tag}")


def worker_log(tag: str) -> str:
    return f"{queue_log_dir()}{tag}.jsonl"


# ── 批次 ────────────────────────────────────────────────────────────────────
def batch_dir(store: str) -> str:
    return f"{BATCHES}{store}/"


def batch_manifest(store: str) -> str:
    return f"{batch_dir(store)}manifest.json"


def batch_patterns(store: str) -> str:
    return f"{batch_dir(store)}patterns.npz"


def batch_results_dir(store: str) -> str:
    return f"{batch_dir(store)}results/"


def batch_result(store: str, rec_id: str) -> str:
    """逐筆結果檔＝合併語義（I-15）：兩台先後寫同一批也不會整份覆蓋。"""
    return f"{batch_results_dir(store)}{rec_id}{_RESULT_EXT}"


# ── runtime ─────────────────────────────────────────────────────────────────
def runtime_dir(profile: str) -> str:
    return f"{RUNTIME_STATE}{profile}/"


def runtime_lock(profile: str) -> str:
    return f"{runtime_dir(profile)}lock"


def strategies_yaml(profile: str) -> str:
    return f"{runtime_dir(profile)}strategies.yaml"


def state_json(profile: str) -> str:
    return f"{runtime_dir(profile)}state.json"


def status_json(profile: str) -> str:
    return f"{runtime_dir(profile)}status.json"


def events_jsonl(profile: str) -> str:
    return f"{runtime_dir(profile)}events.jsonl"


def pending_jsonl(profile: str) -> str:
    return f"{runtime_dir(profile)}pending.jsonl"


def runtime_stop(profile: str) -> str:
    return f"{runtime_dir(profile)}STOP"


def control_json(profile: str) -> str:
    """CLI → runtime 的單向控制檔（resume 等）；runtime 每 tick 開頭消費並刪除。"""
    return f"{runtime_dir(profile)}control.json"


def inflight_dir(profile: str) -> str:
    return f"{runtime_dir(profile)}inflight/"


def inflight_file(profile: str, store: str) -> str:
    return f"{inflight_dir(profile)}{store}.json"


# ── 由列舉結果反推名字 ──────────────────────────────────────────────────────
def stem_of(key: str) -> str:
    """key → 最後一段去掉副檔名（`db/p/<id>-<store>.npz` → `<id>-<store>`）。"""
    return key.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def record_stems(keys) -> set:
    """`list(db_dir(p))` 的結果 → 紀錄檔主幹集合（索引檔／子前綴自動略過）。"""
    return {stem_of(k) for k in keys if k.endswith(_RECORD_EXT)}


def result_ids(keys) -> set:
    """`list(batch_results_dir(s))` 的結果 → 結果 id 集合。"""
    return {stem_of(k) for k in keys if k.endswith(_RESULT_EXT)}


def dir_names(keys) -> list:
    """列舉結果 → 子前綴（目錄）名，排序。"""
    return sorted(k.rstrip("/").rsplit("/", 1)[-1] for k in keys if k.endswith("/"))


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


# ── 本機路徑（不是 depot key：這些東西不抽象） ──────────────────────────────
#? registry.py／strategies/*.py 是**程式碼**，要 runpy／importlib 從本機檔案系統載入；
#  strategy_workdir 是策略的私有暫存（runtime 永不讀它，換後端也不必跟著搬）。
def registry_py(root) -> Path:
    return Path(root) / "registry.py"


def user_strategies_dir(root) -> Path:
    return Path(root) / "strategies"


def strategy_workdir(root, profile: str, strategy: str) -> Path:
    return Path(root) / "runtime_state" / profile / "strategies" / strategy


# ── 整體 ────────────────────────────────────────────────────────────────────
def layout_prefixes() -> tuple:
    """`emforge init` 要 `ensure_prefixes` 的前綴（不含 per-profile 子前綴，那些第一次用到才建）。"""
    return (DB, LEDGER, QUEUE, queue_state_dir(), queue_log_dir(), BATCHES, RUNTIME_STATE)


def snapshot(*, profile: str, store: str, rec_id: str, spec: str, tag: str) -> dict:
    """所有 key 函式的一次性展開——tests/test_paths.py 用它釘快照。"""
    return {
        "db_dir": db_dir(profile),
        "record_file": record_file(profile, rec_id, store),
        "db_index": db_index(profile),
        "db_imported": db_imported(profile),
        "retired_marker": retired_marker(profile),
        "ledger_file": ledger_file(profile, spec),
        "jobs_file": jobs_file(),
        "jobs_lock": jobs_lock(),
        "claim_file": claim_file(store),
        "done_file": done_file(store),
        "fail_file": fail_file(store),
        "queue_stop": queue_stop(),
        "queue_stop_tag": queue_stop(tag),
        "worker_log": worker_log(tag),
        "batch_manifest": batch_manifest(store),
        "batch_patterns": batch_patterns(store),
        "batch_results_dir": batch_results_dir(store),
        "batch_result": batch_result(store, rec_id),
        "runtime_lock": runtime_lock(profile),
        "strategies_yaml": strategies_yaml(profile),
        "state_json": state_json(profile),
        "status_json": status_json(profile),
        "events_jsonl": events_jsonl(profile),
        "pending_jsonl": pending_jsonl(profile),
        "runtime_stop": runtime_stop(profile),
        "control_json": control_json(profile),
        "inflight_dir": inflight_dir(profile),
        "inflight_file": inflight_file(profile, store),
    }


def local_snapshot(root, *, profile: str, strategy: str) -> dict:
    """本機路徑那一節的展開（回 `Path`，不是 key）。"""
    return {
        "registry_py": registry_py(root),
        "user_strategies_dir": user_strategies_dir(root),
        "strategy_workdir": strategy_workdir(root, profile, strategy),
    }
