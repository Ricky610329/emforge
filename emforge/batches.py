"""emforge/batches.py — 一批的落地：`batches/<store>/{manifest.json, patterns.npz, results/<id>.json}`。

- runtime 寫批（patterns 先、manifest 最後＝「批完整」標記）；worker 讀批、逐筆寫結果；runtime 增量收結果。
- 逐筆結果檔＝以 id 為主鍵的合併語義（I-15）；`results(known_ids)` 只開新檔（I-5）。
- `newest_result_at()` 是「這批還在動」的進度心跳（stale-claim 接管看它，不只看 claim 時間）。

儲存一律走 `Depot`（doc＋列舉語義），這個檔不認得檔案系統：建構子吃 `Depot | str | Path`。
"""
import io

import numpy as np

from . import paths
from .depot import open_depot
from .model import now_iso, pack_bits, unpack_bits


class BatchExists(Exception):
    """同名批已存在——防覆寫（store 名要唯一）。"""


# ── 結果檔（批結果與儀器 ad-hoc 結果同一來源，M13） ─────────────────────────
def result_base(rec_id: str, *, attempts: int, machine: str, worker_ver: str, profile_hash: str) -> dict:
    """每筆結果的戳記（I-10：誰、哪個版本、哪個儀器指紋量的）。"""
    return {"id": rec_id, "attempts": int(attempts), "machine": machine, "worker_ver": worker_ver,
            "profile_hash": profile_hash, "at": now_iso()}


def make_result(profile, base: dict, out, elapsed_s: float) -> dict:
    """SimResult → 結果檔 dict：形狀對＝done（原始響應＋time_s＋extra）；形狀錯＝error（bad_response_shape）。"""
    resp = np.asarray(out.response, np.float32)
    expected = (len(profile.labels), profile.n_points)
    if resp.shape != expected:
        return error_result(base, f"bad_response_shape: {resp.shape} ≠ {expected}")
    time_s = float(out.time_s) if out.time_s else float(elapsed_s)
    return {**base, "status": "done", "response": resp.tolist(), "time_s": time_s, "extra": dict(out.extra or {})}


def error_result(base: dict, error: str) -> dict:
    return {**base, "status": "error", "error": str(error)}


class Batch:
    def __init__(self, depot, store: str):
        self.depot, self.store = open_depot(depot), store

    def exists(self) -> bool:
        return self.depot.exists(paths.batch_manifest(self.store))

    # ── 寫批（runtime） ──────────────────────────────────────────────────
    def write(self, manifest: dict, patterns, ids) -> None:
        """patterns.npz 先落、manifest.json 最後。ids 必須與 patterns 一一對應且等於 manifest.items 的順序。"""
        if self.exists():
            raise BatchExists(f"批 {self.store} 已存在")
        patterns, ids = np.asarray(patterns), list(ids)
        if len(ids) != len(patterns) or [it["id"] for it in manifest["items"]] != ids:
            raise ValueError("ids 必須與 patterns 及 manifest.items 一一對應")
        packed = np.stack([pack_bits(p) for p in patterns]) if len(patterns) else np.zeros((0, 0), np.uint8)
        buf = io.BytesIO()
        np.savez(buf, ids=np.array(ids, dtype="<U16"), packed=packed,
                 shape=np.asarray(patterns.shape[1:], np.int64))
        self.depot.put_bytes(paths.batch_patterns(self.store), buf.getvalue())
        self.depot.put_json(paths.batch_manifest(self.store), manifest)

    # ── 讀批（worker） ──────────────────────────────────────────────────
    def manifest(self) -> dict:
        return self.depot.require_json(paths.batch_manifest(self.store))

    def ids(self) -> list:
        return [it["id"] for it in self.manifest()["items"]]

    def n_items(self) -> int:
        return len(self.manifest()["items"])

    def patterns(self) -> dict:
        """{id: bool[H,W]}，依 manifest 順序。"""
        data = self.depot.require_bytes(paths.batch_patterns(self.store))
        with np.load(io.BytesIO(data), allow_pickle=False) as z:
            ids, packed, shape = [str(i) for i in z["ids"]], z["packed"], tuple(int(x) for x in z["shape"])
        return {i: unpack_bits(packed[k], shape) for k, i in enumerate(ids)}

    # ── 結果（worker 寫、runtime 讀） ─────────────────────────────────────
    def write_result(self, rec_id: str, result: dict) -> None:
        """一筆一檔、整份原子替換；重試會覆寫**同一筆**（attempts 遞增），不碰別筆。"""
        self.depot.put_json(paths.batch_result(self.store, rec_id), result)

    def result_ids(self) -> set:
        return paths.result_ids(self.depot.list(paths.batch_results_dir(self.store)))

    def results(self, known_ids=None) -> dict:
        """{id: result}；給 known_ids 就只讀不在裡面的（增量）。
        #? 列舉可最終一致：列到了但還讀不到的先跳過（下一輪再收），不要塞一個 None 進去讓呼叫端炸。"""
        known = set(known_ids or ())
        out = {}
        for i in sorted(self.result_ids() - known):
            r = self.depot.get_json(paths.batch_result(self.store, i))
            if r is not None:
                out[i] = r
        return out

    def newest_result_at(self) -> float | None:
        """最新結果檔的 modified_at（伺服器側時鐘）；沒有結果回 None。"""
        return self.depot.newest(paths.batch_results_dir(self.store))
