"""本機結果待傳區；先原子落地，收到遠端確認後才移除，與求解暫存分開。

#! 回歸 I-15／I-4（2026-09-23）：補傳的合併語義＝「缺或錯不得覆蓋成功」——本機 done 一律勝過遠端非 done（不看 attempts）；
#  回填不了的（身分不相容、批次不存在、已 abandon）搬到 held 分區保留證據，**不拋例外**——以前一筆壞的每圈拋 ValueError，
#  worker 連錯十圈自行退出、重啟照樣再死，同時擋住其他 store 的補傳。
"""
import uuid

from .. import paths
from ..batches import Batch
from ..model import STATUS_DONE, now_iso


class ResultPending(Exception):
    """求解結果已留在本機，等待補傳；不能因此重算或把機器列入失敗名單。"""


class ResultHeld(Exception):
    """這筆待傳結果永遠回填不了（不相容／批次不存在／已 abandon）：搬到 held 分區、記一次事件。"""


def _noop(event, /, **fields):
    return None


def should_write(old, result) -> bool:
    """遠端沒有 → 寫；遠端已 done → 不寫；本機 done → 寫；兩邊都不是 done → attempts 高的贏。"""
    if not old:
        return True
    if old.get("status") == STATUS_DONE:
        return False
    if result.get("status") == STATUS_DONE:
        return True
    return old.get("attempts", 0) < result.get("attempts", 0)


class ResultOutbox:
    def __init__(self, local, depot, machine_tag, log=_noop):
        self.local, self.depot, self.machine_tag, self.log = local, depot, machine_tag, log
        self.owner = "outbox_" + uuid.uuid4().hex

    def save(self, batch, rid, result):
        key = paths.result_outbox_file(self.depot.spec, batch.store, rid)
        old = self.local.get_json(key)
        if old and (old["depot"], old["store"], old["id"]) != (self.depot.spec, batch.store, rid):
            raise ValueError("待傳結果鍵碰撞，保留原檔")
        self.local.put_json(key, {"depot": self.depot.spec, "store": batch.store, "id": rid, "result": result})
        return key

    def publish(self, queue, batch, rid, result):
        key = self.save(batch, rid, result)
        try:
            return self._deliver(queue, key)
        except ResultHeld as e:
            self._hold(key, str(e))
            return True                             # 這筆留下證據；對這批而言不算被接管
        except Exception as e:
            raise ResultPending(f"結果已保留本機，補傳未完成：{type(e).__name__}: {e}") from e

    def flush(self, queue, store=None):
        """認領新工作前補傳；別台仍持有的批次保留，已存在的新結果優先；回填不了的搬去 held。"""
        if queue.depot.spec != self.depot.spec:
            raise ValueError("待傳區與佇列必須指向同一 Depot")
        for key in self.local.list(paths.result_outbox_dir(self.depot.spec)):
            doc = self.local.require_json(key)
            if store is not None and doc["store"] != store:
                continue
            try:
                self._deliver(queue, key)
            except ResultHeld as e:
                self._hold(key, str(e))

    def _hold(self, key, reason: str) -> None:
        doc = self.local.require_json(key)
        held = paths.result_outbox_held_file(self.depot.spec, doc["store"], doc["id"])
        self.local.put_json(held, {**doc, "reason": reason, "held_at": now_iso()})
        self.local.delete(key)
        self.log("outbox_held", store=doc["store"], id=doc["id"], reason=reason)

    def _deliver(self, queue, key):
        doc = self.local.require_json(key)
        if doc["depot"] != self.depot.spec:
            raise ResultHeld("待傳結果屬於另一平台")
        store, rid, result = doc["store"], doc["id"], doc["result"]
        with self.depot.lock(paths.jobs_lock(), owner=self.owner):
            if queue.claim_owner(store) not in (None, self.machine_tag):
                return False
            batch = Batch(self.depot, store)
            if not batch.exists():
                raise ResultHeld("批次不存在，不猜測回填")
            if queue.state(store) == "missing":
                return False                        # 派工半途（inflight 有、佇列沒有）：等 runtime 對帳
            manifest = batch.manifest()
            if result["profile_hash"] != manifest["profile_hash"] or rid not in batch.ids():
                raise ResultHeld("待傳結果與批次身分不相容")
            old = self.depot.get_json(paths.batch_result(store, rid))
            if should_write(old, result):
                if queue.state(store) == "done":
                    raise ResultHeld("批次已 abandon／收尾且無對應成功結果，不回填")
                batch.write_result(rid, result)
            self.local.delete(key)
        return True
