"""查詢 facade：本機與網路都回 Record，不把儲存庫寫入能力交給算法。"""
from .model import Record


def records(rows):
    return [Record.from_meta(r["meta"], r["bits"], r["response"]) for r in rows]


class ClientView:
    def __init__(self, platform, profile, name):
        self.platform, self.profile, self.name = platform, profile, name

    def query(self, **filters):
        return records(self.platform.call("db_query", profile=self.profile, **filters))

    def top(self, k):
        return records(self.platform.call("db_top", profile=self.profile, k=k))

    def sample(self, n, *, seed, **filters):
        return records(self.platform.call("db_sample", profile=self.profile, n=n, seed=seed, **filters))

    def lineage(self, rec_id, depth=10):
        return records(self.platform.call("db_lineage", profile=self.profile, rec_id=rec_id, depth=depth))

    def children(self, rec_id):
        return records(self.platform.call("db_children", profile=self.profile, rec_id=rec_id))

    def runs(self, strategy=None):
        return self.platform.call("db_runs", profile=self.profile, strategy=strategy)

    def mine(self, **filters):
        return self.query(strategy=self.name, **filters)
