"""emforge/batches.py — 一批的落地：`batches/<store>/{manifest.json, patterns.npz, results/<id>.json}`。

- runtime 寫批（patterns 先、manifest 最後＝「批完整」標記）；worker 讀批、逐筆寫結果；runtime 增量收結果。
- 逐筆結果檔＝以 id 為主鍵的合併語義（I-15）；`results(known_ids)` 只開新檔（I-5）。
- `newest_result_mtime()` 是「這批還在動」的進度心跳（stale-claim 接管看它，不只看 claim 時間）。
"""
import io
from pathlib import Path

import numpy as np

from . import fs, paths
from .model import pack_bits, unpack_bits


class BatchExists(Exception):
    """同名批已存在——防覆寫（store 名要唯一）。"""


class Batch:
    def __init__(self, root, store: str):
        self.root, self.store = Path(root), store

    @property
    def dir(self) -> Path:
        return paths.batch_dir(self.root, self.store)

    def exists(self) -> bool:
        return paths.batch_manifest(self.root, self.store).exists()

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
        fs.atomic_write_bytes(paths.batch_patterns(self.root, self.store), buf.getvalue())
        fs.atomic_write_json(paths.batch_manifest(self.root, self.store), manifest)

    # ── 讀批（worker） ──────────────────────────────────────────────────
    def manifest(self) -> dict:
        return fs.read_json(paths.batch_manifest(self.root, self.store))

    def ids(self) -> list:
        return [it["id"] for it in self.manifest()["items"]]

    def n_items(self) -> int:
        return len(self.manifest()["items"])

    def patterns(self) -> dict:
        """{id: bool[H,W]}，依 manifest 順序。"""
        with np.load(paths.batch_patterns(self.root, self.store), allow_pickle=False) as z:
            ids, packed, shape = [str(i) for i in z["ids"]], z["packed"], tuple(int(x) for x in z["shape"])
        return {i: unpack_bits(packed[k], shape) for k, i in enumerate(ids)}

    # ── 結果（worker 寫、runtime 讀） ─────────────────────────────────────
    def write_result(self, rec_id: str, result: dict) -> None:
        """一筆一檔、原子；重試會覆寫**同一筆**（attempts 遞增），不碰別筆。"""
        fs.atomic_write_json(paths.batch_result(self.root, self.store, rec_id), result)

    def result_ids(self) -> set:
        d = paths.batch_results_dir(self.root, self.store)
        return {p.stem for p in d.glob("*.json")} if d.is_dir() else set()

    def results(self, known_ids=None) -> dict:
        """{id: result}；給 known_ids 就只讀不在裡面的（增量）。"""
        known = set(known_ids or ())
        return {i: fs.read_json(paths.batch_result(self.root, self.store, i)) for i in sorted(self.result_ids() - known)}

    def newest_result_mtime(self) -> float | None:
        return fs.newest_mtime(paths.batch_results_dir(self.root, self.store), "*.json")
