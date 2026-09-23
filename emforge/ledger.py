"""emforge/ledger.py — 榜（一 (profile, spec) 一檔）、待審（pending.jsonl）、rescore。

- 榜只能經 `Ledger.promote`／`rescore` 寫；檔帶 `_checksum`，手改會在下一次讀取被抓（LedgerTamper）。
  這是「繞過會留紀錄」而非「不可能」（architecture §12）。
- history append-only；`best` 永遠是保守值（公證 min）。
- 換評估器＝`rescore` 建新榜（零重量），舊榜一個 byte 都不動（D3）。

儲存一律走 `Depot`（榜＝doc、pending＝log），這個檔不認得檔案系統：建構子吃 `Depot | str | Path`。
"""
from . import paths, specs
from .depot import open_depot
from .model import STATUS_DONE, Profile, Spec, canonical_json, now_iso, sha1_hex


class LedgerTamper(Exception):
    """checksum 不符：榜檔被 promote／rescore 以外的路徑改過。"""


class LedgerExists(Exception):
    pass


class NotPending(Exception):
    """候選沒過公證（不在 pending.jsonl）。"""


class UnknownRecord(Exception):
    pass


def _checksum(doc: dict) -> str:
    return sha1_hex(canonical_json({k: v for k, v in doc.items() if k != "_checksum"}).encode("utf-8"))[:16]


class Pending:
    """公證通過、等人審的候選。runtime 寫、人／AI 讀、promote 查。"""

    def __init__(self, depot, profile: str):
        self.depot, self.key = open_depot(depot), paths.pending_jsonl(profile)

    def append(self, entry: dict) -> None:
        self.depot.append(self.key, entry)

    def list(self) -> list:
        return self.depot.read_log(self.key)

    def has(self, rec_id: str) -> bool:
        return self.get(rec_id) is not None

    def get(self, rec_id: str) -> dict | None:
        hits = [e for e in self.list() if e.get("id") == rec_id]
        return hits[-1] if hits else None


class Ledger:
    def __init__(self, depot, profile: str, spec: str):
        self.depot, self.profile, self.spec = open_depot(depot), profile, spec
        self.key = paths.ledger_file(profile, spec)

    @property
    def where(self) -> str:
        """人讀的位置（錯誤訊息用）——後端無關。"""
        return f"{self.depot.spec}/{self.key}"

    def exists(self) -> bool:
        return self.depot.exists(self.key)

    def read(self) -> dict:
        doc = self.depot.require_json(self.key)
        if doc.get("_checksum") != _checksum(doc):
            raise LedgerTamper(f"{self.where} checksum 不符——榜檔被手改？榜只能經 promote／rescore 寫")
        return doc

    def _new(self) -> dict:
        return {"profile": self.profile, "spec": self.spec, "best": None, "history": []}

    def _write(self, doc: dict) -> None:
        doc["_checksum"] = _checksum(doc)
        self.depot.put_json(self.key, doc)

    def _conservative(self, ms: list, entry: dict | None) -> float:
        """分數用**本榜的 spec** 對 db 量測重算取 min（review：--spec 別的規格時不能抄 pending 裡 profile 規格的分數）；
        spec 沒註冊才退回 pending 的保守值／量測 min。"""
        try:
            spec = specs.get_spec(self.spec)
        except specs.UnknownSpec:
            spec = None
        if spec is not None:
            vals = [s for s in (spec.score(r.measure) for r in ms) if s is not None]
            if not vals:
                #! 回歸 I-28（2026-09-23）：以前退回抄 pending 裡 profile 規格的分數寫進本榜——換尺算不出分就是不能上榜
                raise ValueError(f"{self.spec} 對這些量測算不出分數（全被門檻擋），不能 promote 上這個榜")
            return min(vals)
        return entry["conservative"] if entry else min(r.score for r in ms)

    def best(self) -> dict | None:
        return self.read()["best"] if self.exists() else None

    def history(self) -> list:
        return self.read()["history"] if self.exists() else []

    def promote(self, rec_id: str, *, by: str, db, pending: Pending, force: bool = False, note: str = "") -> dict:
        """換王：id 必須在 db 且在 pending（--force 可繞，但記錄 force=true）。分數＝公證保守值。"""
        if not by:
            raise ValueError("promote 必須帶 by（誰換的王）")
        ms = [r for r in db.measurements(self.profile, rec_id) if r.status == STATUS_DONE and r.score is not None]
        if not ms:
            raise UnknownRecord(f"{rec_id} 不在 db/{self.profile}/（或沒有量成功的紀錄）")
        entry = pending.get(rec_id)
        if entry is None and not force:
            raise NotPending(f"{rec_id} 不在 pending.jsonl——沒過公證；確定要就 force（會記錄）")
        conservative = self._conservative(ms, entry)
        with self.depot.lock(paths.ledger_lock(self.profile, self.spec), owner=f"promote:{by}"):
            doc = self.read() if self.exists() else self._new()
            prev = doc["best"]
            best = {"id": rec_id, "score": conservative, "at": now_iso(), "by": by, "note": note, "force": entry is None}
            doc["best"] = best
            doc["history"].append({"event": "promote", "id": rec_id, "score": conservative,
                                   "prev_id": prev["id"] if prev else None, "prev_score": prev["score"] if prev else None,
                                   "at": best["at"], "by": by, "note": note, "force": entry is None})
            self._write(doc)
        return best


def rescore(depot, profile: Profile, spec: Spec, db, *, by: str, force: bool = False) -> dict:
    """為 spec 建（或重算）榜：掃 db/<profile>/ 每筆 done 用 Record.measure 重算 score（零重量、不重寫 Record 檔）；
    首任王＝保守值（同 id 取 min）最高者。榜已存在需 force，且只 append history。"""
    if spec.measure != profile.measure:
        raise ValueError(f"spec {spec.name} 的 measure={spec.measure!r} ≠ profile 的 {profile.measure!r}——換尺＝換儀器，不是 rescore")
    lg = Ledger(depot, profile.name, spec.name)
    if lg.exists() and not force:
        raise LedgerExists(f"榜 {lg.where} 已存在；要重算請 force")
    conservative: dict = {}
    for ln in db.metas(profile.name):
        if ln["status"] != STATUS_DONE:
            continue
        rec = db.load(profile.name, ln["stem"])
        s = spec.score(rec.measure)
        if s is not None:
            conservative[rec.id] = min(conservative.get(rec.id, s), s)
    now = now_iso()
    best = None
    if conservative:
        bid = max(sorted(conservative), key=lambda i: conservative[i])
        best = {"id": bid, "score": conservative[bid], "at": now, "by": by, "note": "rescore", "force": bool(force)}
    with lg.depot.lock(paths.ledger_lock(profile.name, spec.name), owner=f"rescore:{by}"):
        doc = lg.read() if lg.exists() else lg._new()
        prev = doc["best"]
        doc["best"] = best
        doc["history"].append({"event": "rescore", "n": len(conservative), "id": best["id"] if best else None,
                               "score": best["score"] if best else None, "prev_id": prev["id"] if prev else None,
                               "prev_score": prev["score"] if prev else None, "at": now, "by": by, "note": "rescore",
                               "force": bool(force)})
        lg._write(doc)
    return {"spec": spec.name, "n": len(conservative), "best": best}
