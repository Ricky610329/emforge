"""emforge/depot/file.py — `FileDepot`：`Depot` 的檔案系統後端；key → `root/key`，逐位元重現今天的磁碟佈局。

假設（NTFS 本機與 SMB2+ 皆成立；舊系統以此在 NAS 上跑了兩個月）：`open(O_CREAT|O_EXCL)` 原子、同目錄 `os.replace` 原子、
server mtime 可信且各機 NTP 同步（`selfcheck` 量偏移）；SMB 多寫者 `O_APPEND` 交錯無保證 → 日誌單寫者。
租約＝claim 檔（JSON `{"owner","at",…}`）；心跳＝`os.utime`；破鎖＝`os.replace(path → <name>.broken.<pid>.<rand>)`＋mtime 身分檢查
（remove 會刪到對方剛建好的**新**鎖，I-3 變體），證據檔留著給 doctor 數。
#! Windows 上兩個並發 rename 可以**都成功**（第二個經由先開好的 handle 把已改名的同一檔再改一次；契約測試抓到的）——
#  所以破鎖不是仲裁、`claim`（O_EXCL）才是；破鎖只保證「搬到的不是別人的新鎖」（mtime ≠ 判過期的那個就放回去）。
"""
import json
import os
import random
import time
from pathlib import Path, PurePosixPath, PureWindowsPath

from .. import fs
from ..model import now_iso
from .base import Depot

CLOCK_SKEW_WARN_S = 30.0


def _probe_mtime(path) -> float:
    return os.path.getmtime(path)


def _undo_break(broken: Path, path: Path) -> bool:
    """把誤搬的新鎖放回原位；原位已被第三方認領就不覆蓋（Windows rename 目標存在即拋；POSIX 用 link 取得同樣語義）。"""
    try:
        if os.name == "nt":
            os.rename(broken, path)
        else:
            os.link(broken, path)
            os.unlink(broken)
        return True
    except (FileExistsError, FileNotFoundError, PermissionError):
        return False


class FileDepot(Depot):
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.spec = "file://" + self.root.as_posix()

    def __repr__(self) -> str:
        return f"FileDepot({str(self.root)!r})"

    def path(self, key: str) -> Path:
        """key → 本機路徑（只給 File 專屬呼叫端：doctor 的磁碟檢查、測試）。check_key 之外再擋一次絕對／帶磁碟機的 key（I-26）。"""
        key = self.check_key(key)
        if PureWindowsPath(key).drive or PureWindowsPath(key).is_absolute() or PurePosixPath(key).is_absolute():
            raise ValueError(f"壞 key：{key!r}")
        return self.root / key

    # ── 文件 ─────────────────────────────────────────────────────────────────
    def put_bytes(self, key, data):
        fs.atomic_write_bytes(self.path(key), data)

    def get_bytes(self, key):
        return fs.read_bytes(self.path(key))

    def get_json(self, key):
        return fs.read_json(self.path(key), None)  # 保留舊語義：替換瞬間短暫重試，用盡 FsCorrupt

    def delete(self, key):
        return fs.release(self.path(key))

    def exists(self, key):
        return self.path(key).is_file()

    # ── 列舉／時間 ───────────────────────────────────────────────────────────
    def list(self, prefix):
        self.check_prefix(prefix)
        d = self.root / prefix if prefix else self.root
        if not d.is_dir():
            return []
        out = []
        with os.scandir(d) as it:
            for e in it:
                if e.name.startswith(".") or ".broken." in e.name:
                    continue  # tmp／探針／破鎖證據＝後端內部，不是 key
                out.append(prefix + e.name + ("/" if e.is_dir() else ""))
        return sorted(out)

    def modified_at(self, key):
        return fs.mtime(self.path(key))

    def set_modified_at(self, key, epoch):
        os.utime(self.path(key), (epoch, epoch))

    # ── 日誌 ─────────────────────────────────────────────────────────────────
    def append(self, key, record):
        fs.append_jsonl(self.path(key), record)

    def read_log(self, key):
        return fs.read_jsonl(self.path(key))

    def rewrite_log(self, key, records):
        text = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records)
        fs.atomic_write_bytes(self.path(key), text.encode("utf-8"))

    # ── 租約 ─────────────────────────────────────────────────────────────────
    def claim(self, key, payload):
        return fs.try_claim(self.path(key), self.check_lease_payload(payload))

    def owner(self, key):
        return fs.read_claim(self.path(key))

    def touch(self, key):
        try:
            os.utime(self.path(key), None)
            return True
        except FileNotFoundError:
            return False

    def release(self, key, owner=None):
        p = self.path(key)
        if owner is not None:
            cur = fs.read_claim(p)
            if cur is None:
                return not p.exists()  # 缺＝已經沒有；有檔但認不出誰的 → 不動
            if cur.get("owner") != owner:
                return False
        fs.release(p)
        return True

    def break_if_stale(self, key, stale_s, now=None):
        p = self.path(key)
        m0 = fs.mtime(p)
        if m0 is None or ((self.now() if now is None else now) - m0) <= stale_s:
            return False
        broken = p.with_name(f"{p.name}.broken.{os.getpid()}.{random.randrange(16**4):04x}")
        try:
            os.replace(p, broken)
        except (FileNotFoundError, PermissionError):
            return False  # 別人先破了／正在寫
        m1 = fs.mtime(broken)
        if m1 is not None and m1 != m0:
            #! 檢查→replace 之間別人已破鎖並重新認領：搬到的是那把**新**鎖（mtime 不是我們判過期的那個）。
            #  把它放回去（目標已存在就不覆蓋），這次算沒破到。
            _undo_break(broken, p)
            return False
        return True

    # ── 其他 ─────────────────────────────────────────────────────────────────
    def ensure_prefixes(self, prefixes):
        for prefix in prefixes:
            (self.root / self.check_prefix(prefix)).mkdir(parents=True, exist_ok=True)

    def selfcheck(self):
        problems = []
        probe = f".selfcheck.{os.getpid()}.{random.randrange(16**4):04x}"
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            if not self.claim(probe, {"owner": "selfcheck", "at": now_iso()}):
                problems.append(f"根目錄不可寫（claim 探針失敗）：{self.root}")
                return problems
            skew = _probe_mtime(self.path(probe)) - time.time()
            if abs(skew) > CLOCK_SKEW_WARN_S:
                problems.append(f"時鐘偏移 {skew:+.0f}s（clock skew：伺服器 mtime vs 本機 time.time()）")
            self.release(probe)
        except OSError as e:
            problems.append(f"根目錄不可寫：{self.root}（{e}）")
        return problems
