"""本機結果待傳區；先原子落地，收到遠端確認後才移除，與求解暫存分開。"""
import uuid

from .. import paths
from ..batches import Batch


class ResultPending(Exception):
    """求解結果已留在本機，等待補傳；不能因此重算或把機器列入失敗名單。"""


class ResultOutbox:
    def __init__(self, local, depot, machine_tag):
        self.local, self.depot, self.machine_tag = local, depot, machine_tag
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
        except Exception as e:
            raise ResultPending(f"結果已保留本機，補傳未完成：{type(e).__name__}: {e}") from e

    def flush(self, queue, store=None):
        """認領新工作前補傳；別台仍持有的批次保留，已存在的新結果優先。"""
        if queue.depot.spec != self.depot.spec:
            raise ValueError("待傳區與佇列必須指向同一 Depot")
        for key in self.local.list(paths.result_outbox_dir(self.depot.spec)):
            doc = self.local.require_json(key)
            if store is None or doc["store"] == store:
                self._deliver(queue, key)

    def _deliver(self, queue, key):
        doc = self.local.require_json(key)
        if doc["depot"] != self.depot.spec:
            raise ValueError("待傳結果屬於另一平台")
        store, rid, result = doc["store"], doc["id"], doc["result"]
        with self.depot.lock(paths.jobs_lock(), owner=self.owner):
            if queue.claim_owner(store) not in (None, self.machine_tag):
                return False
            batch = Batch(self.depot, store)
            if not batch.exists() or queue.state(store) == "missing":
                return False
            manifest = batch.manifest()
            if result["profile_hash"] != manifest["profile_hash"] or rid not in batch.ids():
                raise ValueError("待傳結果與批次身分不相容")
            old = self.depot.get_json(paths.batch_result(store, rid))
            if not old or (old.get("status") != "done" and old.get("attempts", 0) < result["attempts"]):
                if queue.state(store) == "done":
                    return False  # 人工 abandon 不再回填；保留本機證據
                batch.write_result(rid, result)
            self.local.delete(key)
        return True
