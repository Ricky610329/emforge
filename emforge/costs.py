"""算法提出、共用與新增量測分帳；補測及公證不當獨立樣本。"""
from . import paths, submissions
from .db import Database


def usage(depot, profile, run_id):
    docs = [d for d in submissions.documents(depot, profile) if d["run_id"] == run_id]
    charged, shared = set(), 0
    for doc in docs:
        for ref in submissions.resolve(depot, doc).get("items", []):
            if ref.get("store"):
                if ref.get("shared"):
                    shared += 1
                else:
                    charged.add((ref["store"], ref["id"]))
    db = Database(depot)
    rows = [r for r in db.view(profile).query(run_id=run_id) if r.kind == "sample"]
    charged.update((r.run["store"], r.id) for r in rows)
    for key in depot.list(paths.inflight_dir(profile)):
        inf = depot.get_json(key)
        if inf and inf["kind"] == "sample":
            charged.update((inf["store"], rid) for rid, item in inf["items"].items() if item.get("run_id") == run_id)
    return {"proposed": sum(len(d["items"]) for d in docs), "shared": shared,
            "new_measurements": len(charged),
            "measured_time_s": sum(float(r.run.get("time_s") or 0) for r in rows),
            "time_scope": "已保存的 sample 量測耗時；未保存的失敗嘗試不在此值內"}
