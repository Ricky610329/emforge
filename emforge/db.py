"""emforge/db.py — 共享資料庫：一筆一檔（.npz）、增量索引（_index.jsonl）、唯讀 View。

- 一筆＝`db/<profile>/<id>-<store>.npz`（bits packbits、response、meta JSON），整份原子替換，**永不覆寫**（I-15）。
- 索引＝`_index.jsonl`：入庫 append 一行；開庫只補「磁碟有、索引無」的檔（I-5：不重掃全史）。
- `Database(write_profile=…)` 只寫自己綁的 profile（含 refresh 寫索引）；讀任何 profile 都可以（§7）。
- 讀的一半是 `Reader`；策略拿到的 `View` **只持 Reader**——結構上碰不到 `add`（D7；檢查 #11 之前 `View._db` 就是整個 Database）。
- 讀不到／解不開的紀錄（部分複製、備份軟體獨佔、最終一致列舉）**跳過並記到 `unreadable`**，不讓單一壞檔停掉實例（檢查 #4）。

儲存一律走 `Depot`（doc／log／列舉三種語義），這個檔不認得檔案系統：建構子吃 `Depot | str | Path`。
"""
import io
import json
import zipfile

import numpy as np

from . import paths
from .depot import FsBusy, FsCorrupt, open_depot
from .model import STATUS_DONE, Record, pack_bits, record_id, unpack_bits

INDEX_FIELDS = ("id", "sim_profile", "status", "score", "strategy", "arm", "parent", "tick", "kind", "tag", "run_id")
_UNREADABLE = (OSError, ValueError, KeyError, zipfile.BadZipFile, FsBusy, FsCorrupt)   # 缺檔／壞 zip／非 npz／半截 JSON


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
    with np.load(io.BytesIO(depot.require_bytes(key)), allow_pickle=False) as z:
        meta = json.loads(str(z["meta"]))
        shape = tuple(int(x) for x in z["shape"])
        bits = unpack_bits(z["bits"], shape)
        response = np.asarray(z["response"]) if bool(z["has_response"]) else None
    return Record.from_meta(meta, bits, response)


def _index_line(stem: str, meta: dict) -> dict:
    line = {"stem": stem, "store": meta["run"]["store"], "worker_ver": meta["run"].get("worker_ver")}
    line.update({k: meta.get(k) for k in INDEX_FIELDS})
    return line


# ── Reader（唯讀的一半） ──────────────────────────────────────────────────────
class Reader:
    """索引快取＋載入。`Database` 與 `View` 共用同一個實例（同一份快取）；這裡沒有任何寫入方法。"""

    def __init__(self, depot, index: dict, unreadable: list):
        self.depot, self._index, self.unreadable = depot, index, unreadable

    def _lines(self, profile: str) -> dict:
        if profile not in self._index:
            self._index[profile] = {ln["stem"]: ln for ln in self.depot.read_log(paths.db_index(profile))}
        return self._index[profile]

    def metas(self, profile: str) -> list:
        return list(self._lines(profile).values())

    def ids(self, profile: str, status: tuple = (STATUS_DONE,)) -> set:
        """去重用：預設只認量成功的（error 的 id 要能被再次提案）。"""
        return {ln["id"] for ln in self._lines(profile).values() if ln["status"] in status}

    def load(self, profile: str, stem: str) -> Record:
        """嚴格版：呼叫端要的就是「一定在」（缺／壞直接拋）。"""
        return _load_npz(self.depot, paths.record_by_stem(profile, stem))

    def try_load(self, profile: str, stem: str):
        """容忍版：讀不到／解不開 → 記到 `unreadable` 回 None。列舉可最終一致、備份軟體會獨佔檔（檢查 #4）。"""
        try:
            return _load_npz(self.depot, paths.record_by_stem(profile, stem))
        except _UNREADABLE:
            if (profile, stem) not in self.unreadable:
                self.unreadable.append((profile, stem))
            return None

    def measurements(self, profile: str, rec_id: str) -> list:
        recs = (self.try_load(profile, ln["stem"]) for ln in self._lines(profile).values() if ln["id"] == rec_id)
        return [r for r in recs if r is not None]

    def profiles(self) -> list:
        return paths.dir_names(self.depot.list(paths.DB))


# ── Database ────────────────────────────────────────────────────────────────
class Database:
    def __init__(self, depot, write_profile: str | None = None):
        self.depot = open_depot(depot)
        self.write_profile = write_profile
        self._index: dict = {}   # profile → {stem: index line}
        self.unreadable: list = []   # [(profile, stem)]：這個實例遇過、跳過的紀錄
        self.reader = Reader(self.depot, self._index, self.unreadable)

    # 讀的一半全部委派給 Reader
    def metas(self, profile: str) -> list:
        return self.reader.metas(profile)

    def ids(self, profile: str, status: tuple = (STATUS_DONE,)) -> set:
        return self.reader.ids(profile, status)

    def load(self, profile: str, stem: str) -> Record:
        return self.reader.load(profile, stem)

    def try_load(self, profile: str, stem: str):
        return self.reader.try_load(profile, stem)

    def measurements(self, profile: str, rec_id: str) -> list:
        return self.reader.measurements(profile, rec_id)

    def profiles(self) -> list:
        return self.reader.profiles()

    def view(self, profile: str, strategy: str | None = None) -> "View":
        return View(self.reader, profile, strategy)

    def _check_write(self, profile: str) -> None:
        if self.write_profile is not None and profile != self.write_profile:
            raise ProfileWriteRefused(f"實例綁 {self.write_profile}，拒寫 {profile}")

    def add(self, rec: Record) -> bool:
        """寫一筆。同 id 同 store 已存在 → False（保留先到的）；id 與 bits+profile 不符 → ValueError。"""
        self._check_write(rec.sim_profile)
        if rec.id != record_id(rec.bits, rec.sim_profile):
            raise ValueError(f"Record.id {rec.id} 與 bits+profile 算出的 {record_id(rec.bits, rec.sim_profile)} 不符")
        stem = paths.record_stem(rec.id, rec.run["store"])
        key = paths.record_by_stem(rec.sim_profile, stem)
        if self.depot.exists(key):
            return False
        _save_npz(self.depot, key, rec)
        line = _index_line(stem, rec.meta())
        self.depot.append(paths.db_index(rec.sim_profile), line)
        self.reader._lines(rec.sim_profile)[stem] = line
        return True

    def refresh(self, profile: str) -> int:
        """把索引與磁碟對齊：別的寫者加的先從索引檔合併；索引沒有的檔才載入（回載入筆數）；
        讀不到的跳過（記 `unreadable`）；索引有而檔**確認**不在（`exists`）的才剔除——不靠「列舉為空」（可最終一致）。"""
        self._check_write(profile)
        index_key = paths.db_index(profile)
        lines = self.reader._lines(profile)
        for ln in self.depot.read_log(index_key):
            lines.setdefault(ln["stem"], ln)
        on_disk = paths.record_stems(self.depot.list(paths.db_dir(profile)))
        added = 0
        for stem in sorted(on_disk - set(lines)):
            rec = self.reader.try_load(profile, stem)
            if rec is None:
                continue
            line = _index_line(stem, rec.meta())
            self.depot.append(index_key, line)
            lines[stem] = line
            added += 1
        gone = [stem for stem in set(lines) - on_disk if not self.depot.exists(paths.record_by_stem(profile, stem))]
        if gone:
            for stem in gone:
                del lines[stem]
            self.depot.rewrite_log(index_key, list(lines.values()))
        return added


# ── View（唯讀） ─────────────────────────────────────────────────────────────
class View:
    """策略看到的資料庫。只持 `Reader`：寫入方法不存在（不是被禁、是沒有）。讀不到的紀錄跳過（記在 Reader.unreadable）。"""

    def __init__(self, reader: Reader, profile: str, strategy: str | None = None):
        self._reader, self._profile, self._strategy = reader, profile, strategy

    @property
    def profile(self) -> str:
        return self._profile

    def _load_all(self, profile: str, lines: list) -> list:
        recs = (self._reader.try_load(profile, ln["stem"]) for ln in lines)
        return [r for r in recs if r is not None]

    def query(self, profile: str | None = None, strategy: str | None = None, arm: str | None = None,
              status=None, since_tick: int | None = None, limit: int | None = None,
              tag=None, run_id=None, parent=None) -> list:
        """依 tick 升冪；status 可為字串或 tuple；since_tick 含。"""
        profile = profile or self._profile
        statuses = (status,) if isinstance(status, str) else status
        lines = [ln for ln in self._reader.metas(profile)
                 if (strategy is None or ln["strategy"] == strategy)
                 and (arm is None or ln["arm"] == arm)
                 and (tag is None or ln.get("tag") == tag)
                 and (run_id is None or ln.get("run_id") == run_id)
                 and (parent is None or ln.get("parent") == parent)
                 and (statuses is None or ln["status"] in statuses)
                 and (since_tick is None or (ln["tick"] is not None and ln["tick"] >= since_tick))]
        lines.sort(key=lambda ln: (ln["tick"] if ln["tick"] is not None else -1, ln["stem"]))
        if limit is not None:
            lines = lines[:limit]
        return self._load_all(profile, lines)

    def top(self, k: int, profile: str | None = None) -> list:
        """已量成功、id 不重複、每個 id 取**保守值**（多次量測的 min），依分數降冪。"""
        profile = profile or self._profile
        best: dict = {}
        for ln in self._reader.metas(profile):
            if ln["status"] != STATUS_DONE or ln["score"] is None:
                continue
            cur = best.get(ln["id"])
            if cur is None or ln["score"] < cur["score"]:
                best[ln["id"]] = ln
        ranked = sorted(best.values(), key=lambda ln: (-ln["score"], ln["id"]))
        out = []
        for ln in ranked:
            rec = self._reader.try_load(profile, ln["stem"])
            if rec is not None:
                out.append(rec)
            if len(out) >= k:
                break
        return out

    def mine(self, status=None, since_tick: int | None = None, run_id=None) -> list:
        """本策略自己產出的紀錄（含 error）；有狀態策略靠這個拿上批回饋。"""
        if not self._strategy:
            raise ValueError("View 未綁定策略，mine() 無意義")
        return self.query(strategy=self._strategy, status=status, since_tick=since_tick, run_id=run_id)

    def measurements(self, rec_id: str, profile: str | None = None) -> list:
        return self._reader.measurements(profile or self._profile, rec_id)

    def _lines(self, **filters) -> list:
        """與 `query` 同一套過濾，但只回索引行（不載入 npz）；依 tick 升冪。
        #! 回歸 I-5（2026-09-23）：sample／lineage／runs／children 以前 `query()` 全載入再篩——每次呼叫掃一遍 NAS 全部 npz。"""
        profile = filters.pop("profile", None) or self._profile
        status = filters.pop("status", None)
        statuses = (status,) if isinstance(status, str) else status
        since_tick = filters.pop("since_tick", None)
        lines = [ln for ln in self._reader.metas(profile)
                 if all(ln.get(k) == v for k, v in filters.items() if v is not None)
                 and (statuses is None or ln["status"] in statuses)
                 and (since_tick is None or (ln["tick"] is not None and ln["tick"] >= since_tick))]
        lines.sort(key=lambda ln: (ln["tick"] if ln["tick"] is not None else -1, ln["stem"]))
        return lines

    def sample(self, n: int, *, seed: int, **filters) -> list:
        """過濾後按內容去重取樣；重測不增加抽中機率。只載入抽中的那幾筆。"""
        if n < 0:
            raise ValueError("n 必須非負")
        profile = filters.get("profile") or self._profile
        rows = self._unique_lines(self._lines(**filters))
        picks = np.random.default_rng(seed).choice(len(rows), min(n, len(rows)), replace=False)
        return self._load_all(profile, [rows[i] for i in picks])

    @staticmethod
    def _unique_lines(lines) -> list:
        """每個 id 取 tick 最早的一筆（排除重測），依 id 排序——與舊 `_unique(records)` 同一順序（seed 決定性不變）。"""
        out = {}
        for ln in lines:
            if ln["kind"] != "repeat":
                out.setdefault(ln["id"], ln)
        return [out[k] for k in sorted(out)]

    def children(self, rec_id: str) -> list:
        return self._load_all(self._profile, self._unique_lines(self._lines(parent=rec_id)))

    def lineage(self, rec_id: str, depth: int = 10) -> list:
        """包含自身；原始提出關係優先，忽略重測自親代，遇環即停。先在索引上走親代鏈，只載入鏈上的紀錄。"""
        if depth < 0:
            raise ValueError("depth 必須非負")
        rows = {ln["id"]: ln for ln in self._unique_lines(self._lines())}
        chain, seen = [], set()
        while rec_id in rows and rec_id not in seen and len(chain) < depth:
            seen.add(rec_id)
            chain.append(rows[rec_id])
            rec_id = rows[rec_id].get("parent")
        out = []
        for ln in chain:
            rec = self._reader.try_load(self._profile, ln["stem"])
            if rec is None:
                break                               # 讀不到就到此為止（與舊行為一致：鏈斷）
            out.append(rec)
        return out

    def runs(self, strategy=None) -> list[str]:
        return sorted({ln["run_id"] for ln in self._lines(strategy=strategy) if ln.get("run_id")})
