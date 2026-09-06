"""emforge/db.py — 共享資料庫：一筆一檔（.npz）、增量索引（_index.jsonl）、唯讀 View。

- 一筆＝`db/<profile>/<id>-<store>.npz`（bits packbits、response、meta JSON），整份原子替換，**永不覆寫**（I-15）。
- 索引＝`_index.jsonl`：入庫 append 一行；開庫只補「磁碟有、索引無」的檔（I-5：不重掃全史）。
- `Database(write_profile=…)` 只寫自己綁的 profile；讀任何 profile 都可以（§7）。
- 策略拿到的是 `View`——結構上沒有寫入方法（D7）。

儲存一律走 `Depot`（doc／log／列舉三種語義），這個檔不認得檔案系統：建構子吃 `Depot | str | Path`。
"""
import io
import json

import numpy as np

from . import paths
from .depot import open_depot
from .model import STATUS_DONE, Record, pack_bits, record_id, unpack_bits

INDEX_FIELDS = ("id", "sim_profile", "status", "score", "strategy", "arm", "parent", "tick", "kind")


class ProfileWriteRefused(Exception):
    """實例只寫自己綁定的 profile。"""


# ── 檔案格式 ────────────────────────────────────────────────────────────────
def _save_npz(depot, key: str, rec: Record) -> None:
    buf = io.BytesIO()
    has_resp = rec.response is not None
    np.savez(buf,
             bits=pack_bits(rec.bits),
             shape=np.asarray(rec.bits.shape, np.int64),
             response=np.asarray(rec.response, np.float32) if has_resp else np.zeros((0,), np.float32),
             has_response=np.asarray(has_resp),
             meta=np.asarray(json.dumps(rec.meta(), ensure_ascii=False, sort_keys=True)))
    depot.put_bytes(key, buf.getvalue())


def _load_npz(depot, key: str) -> Record:
    data = depot.get_bytes(key)
    if data is None:
        raise FileNotFoundError(f"{depot.spec}/{key}")
    with np.load(io.BytesIO(data), allow_pickle=False) as z:
        meta = json.loads(str(z["meta"]))
        shape = tuple(int(x) for x in z["shape"])
        bits = unpack_bits(z["bits"], shape)
        response = np.asarray(z["response"]) if bool(z["has_response"]) else None
    return Record.from_meta(meta, bits, response)


def _index_line(stem: str, meta: dict) -> dict:
    line = {"stem": stem, "store": meta["run"]["store"], "worker_ver": meta["run"].get("worker_ver")}
    line.update({k: meta[k] for k in INDEX_FIELDS})
    return line


# ── Database ────────────────────────────────────────────────────────────────
class Database:
    def __init__(self, depot, write_profile: str | None = None):
        self.depot = open_depot(depot)
        self.write_profile = write_profile
        self._index: dict = {}   # profile → {stem: index line}

    def _lines(self, profile: str) -> dict:
        if profile not in self._index:
            self._index[profile] = {ln["stem"]: ln for ln in self.depot.read_log(paths.db_index(profile))}
        return self._index[profile]

    def add(self, rec: Record) -> bool:
        """寫一筆。同 id 同 store 已存在 → False（保留先到的）；id 與 bits+profile 不符 → ValueError。"""
        if self.write_profile is not None and rec.sim_profile != self.write_profile:
            raise ProfileWriteRefused(f"實例綁 {self.write_profile}，拒寫 {rec.sim_profile}")
        if rec.id != record_id(rec.bits, rec.sim_profile):
            raise ValueError(f"Record.id {rec.id} 與 bits+profile 算出的 {record_id(rec.bits, rec.sim_profile)} 不符")
        stem = paths.record_stem(rec.id, rec.run["store"])
        key = paths.record_by_stem(rec.sim_profile, stem)
        if self.depot.exists(key):
            return False
        _save_npz(self.depot, key, rec)
        line = _index_line(stem, rec.meta())
        self.depot.append(paths.db_index(rec.sim_profile), line)
        self._lines(rec.sim_profile)[stem] = line
        return True

    def refresh(self, profile: str) -> int:
        """把索引與磁碟對齊：別的寫者加的先從索引檔合併；索引沒有的檔才載入（回載入筆數）；消失的檔剔除。"""
        index_key = paths.db_index(profile)
        lines = self._lines(profile)
        for ln in self.depot.read_log(index_key):
            lines.setdefault(ln["stem"], ln)
        on_disk = paths.record_stems(self.depot.list(paths.db_dir(profile)))
        added = 0
        for stem in sorted(on_disk - set(lines)):
            line = _index_line(stem, _load_npz(self.depot, paths.record_by_stem(profile, stem)).meta())
            self.depot.append(index_key, line)
            lines[stem] = line
            added += 1
        vanished = set(lines) - on_disk
        if vanished:
            for stem in vanished:
                del lines[stem]
            self.depot.rewrite_log(index_key, list(lines.values()))
        return added

    def metas(self, profile: str) -> list:
        return list(self._lines(profile).values())

    def ids(self, profile: str, status: tuple = (STATUS_DONE,)) -> set:
        """去重用：預設只認量成功的（error 的 id 要能被再次提案）。"""
        return {ln["id"] for ln in self._lines(profile).values() if ln["status"] in status}

    def load(self, profile: str, stem: str) -> Record:
        return _load_npz(self.depot, paths.record_by_stem(profile, stem))

    def measurements(self, profile: str, rec_id: str) -> list:
        return [self.load(profile, ln["stem"]) for ln in self._lines(profile).values() if ln["id"] == rec_id]

    def profiles(self) -> list:
        return paths.dir_names(self.depot.list(paths.DB))

    def view(self, profile: str, strategy: str | None = None) -> "View":
        return View(self, profile, strategy)


# ── View（唯讀） ─────────────────────────────────────────────────────────────
class View:
    """策略看到的資料庫。只有讀；寫入方法不存在（不是被禁、是沒有）。"""

    def __init__(self, db: Database, profile: str, strategy: str | None = None):
        self._db, self._profile, self._strategy = db, profile, strategy

    @property
    def profile(self) -> str:
        return self._profile

    def query(self, profile: str | None = None, strategy: str | None = None, arm: str | None = None,
              status=None, since_tick: int | None = None, limit: int | None = None) -> list:
        """依 tick 升冪；status 可為字串或 tuple；since_tick 含。"""
        profile = profile or self._profile
        statuses = (status,) if isinstance(status, str) else status
        lines = [ln for ln in self._db.metas(profile)
                 if (strategy is None or ln["strategy"] == strategy)
                 and (arm is None or ln["arm"] == arm)
                 and (statuses is None or ln["status"] in statuses)
                 and (since_tick is None or (ln["tick"] is not None and ln["tick"] >= since_tick))]
        lines.sort(key=lambda ln: (ln["tick"] if ln["tick"] is not None else -1, ln["stem"]))
        if limit is not None:
            lines = lines[:limit]
        return [self._db.load(profile, ln["stem"]) for ln in lines]

    def top(self, k: int, profile: str | None = None) -> list:
        """已量成功、id 不重複、每個 id 取**保守值**（多次量測的 min），依分數降冪。"""
        profile = profile or self._profile
        best: dict = {}
        for ln in self._db.metas(profile):
            if ln["status"] != STATUS_DONE or ln["score"] is None:
                continue
            cur = best.get(ln["id"])
            if cur is None or ln["score"] < cur["score"]:
                best[ln["id"]] = ln
        ranked = sorted(best.values(), key=lambda ln: (-ln["score"], ln["id"]))[:k]
        return [self._db.load(profile, ln["stem"]) for ln in ranked]

    def mine(self, status=None, since_tick: int | None = None) -> list:
        """本策略自己產出的紀錄（含 error）；有狀態策略靠這個拿上批回饋。"""
        if not self._strategy:
            raise ValueError("View 未綁定策略，mine() 無意義")
        return self.query(strategy=self._strategy, status=status, since_tick=since_tick)

    def measurements(self, rec_id: str, profile: str | None = None) -> list:
        return self._db.measurements(profile or self._profile, rec_id)
