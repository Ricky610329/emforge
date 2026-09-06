"""emforge/fs.py — 檔案系統協調原語。**唯一**知道儲存後端語義的模組（O-3）。

原語：`atomic_write_*`（tmp→replace）、`try_claim/release`（O_EXCL 認領）、`is_stale/newest_mtime`（mtime 心跳）、
`Lock`（認領＋陳鎖破除）、`append_jsonl`（**單寫者**檔）。換 S3／DB 後端時只改這個檔：
claim→條件式 PUT、atomic_write→PUT、mtime→LastModified、Lock→lease。

假設（NTFS 本機與 SMB2+ 皆成立；舊系統以此在 NAS 上跑了兩個月）：
- `open(O_CREAT|O_EXCL)` 原子；同目錄 `os.replace` 原子；server mtime 可信且各機 NTP 同步（`doctor` 量偏移）。
- SMB 上多寫者 `O_APPEND` 交錯**無保證**——所以 `append_jsonl` 只給單寫者檔，多方匯總一律「一方一檔」。
"""
import json
import os
import random
import shutil
import time
from pathlib import Path

from . import model

#? M12b：`now_iso`／`sha1_hex` 搬去 model.py（它們是 schema 不是檔案系統語義）。這裡重匯出讓未遷模組先照舊用，
#  M12c 拆掉 `Lock` 時一起清。
now_iso = model.now_iso
sha1_hex = model.sha1_hex


class LockTimeout(Exception):
    """鎖在 timeout_s 內拿不到（別人的鎖還新鮮）。函式庫層永不 SystemExit。"""


class FsBusy(Exception):
    """os.replace 重試用盡（目標一直被別的行程開著）。"""


class FsCorrupt(Exception):
    """JSON 檔重試後仍解不開（半截檔）。"""


_MISSING = object()
#? Windows/SMB：目標被別人開著讀時 os.replace 拋 PermissionError（sharing violation）；退避重試而不是立刻失敗。
#  CPython 的 open() 在 Windows 不帶 FILE_SHARE_DELETE，讀者只要開著檔、替換就會被擋——所以總退避要夠長（~5 s），
#  單一讀者的一次 open→read→close 只有毫秒級，正式環境同一檔的讀寫競爭也稀疏（一 store 一寫者）。
_REPLACE_BACKOFF_S = (0.02, 0.05, 0.1, 0.2, 0.4, 0.8, 1.6, 1.6)


# ── 原子寫入 ────────────────────────────────────────────────────────────────
def atomic_write_bytes(path, data: bytes) -> None:
    """寫 tmp → flush+fsync → os.replace。讀者永遠看到完整舊檔或完整新檔；失敗不動舊檔、不留 tmp。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{random.randrange(16**6):06x}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        for i, backoff in enumerate(_REPLACE_BACKOFF_S):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if i == len(_REPLACE_BACKOFF_S) - 1:
                    raise FsBusy(f"os.replace 重試 {len(_REPLACE_BACKOFF_S)} 次仍被佔用：{path}")
                time.sleep(backoff)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def atomic_write_json(path, obj) -> None:
    """canonical JSON（sort_keys、不轉義中文、indent=1）。序列化失敗在寫任何東西之前就拋。"""
    data = json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")
    atomic_write_bytes(path, data)


def read_json(path, default=_MISSING, *, retries: int = 6):
    """讀 JSON；缺檔回 default（沒給就拋 FileNotFoundError）；半截／被佔用（替換瞬間）短暫重試，用盡拋 FsCorrupt。"""
    path = Path(path)
    last = None
    for i in range(max(1, retries)):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            if default is not _MISSING:
                return default
            raise
        except (json.JSONDecodeError, PermissionError) as e:
            last = e
            if i < retries - 1:
                time.sleep(0.02 * (i + 1))
    raise FsCorrupt(f"{path}: {last}")


def read_bytes(path, *, retries: int = 6) -> bytes | None:
    """整份讀 bytes；缺檔回 None；被佔用（替換瞬間的 sharing violation）短暫重試，用盡拋 FsBusy。"""
    path = Path(path)
    for i in range(max(1, retries)):
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None
        except PermissionError as e:
            if i == retries - 1:
                raise FsBusy(f"{path}: {e}")
            time.sleep(0.02 * (i + 1))
    return None


# ── jsonl（單寫者） ──────────────────────────────────────────────────────────
def append_jsonl(path, obj) -> None:
    """append 一行。契約：**單寫者**檔（runtime 對自己的 events/pending、worker 對自己的 log/<tag>）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()


def read_jsonl(path) -> list:
    """逐行 JSON → list；空行跳過；缺檔回 []；壞行拋 FsCorrupt。"""
    path = Path(path)
    if not path.exists():
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError as e:
                    raise FsCorrupt(f"{path}:{lineno}: {e}") from e
    return out


# ── 認領 ────────────────────────────────────────────────────────────────────
def try_claim(path, payload: dict) -> bool:
    """O_EXCL 建檔即認領；成功寫入 payload JSON 回 True；檔已存在回 False。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    except PermissionError:
        #! Windows：別人剛 unlink 同名檔、刪除尚未落定（delete pending）時，O_EXCL 建檔回 ACCESS_DENIED 而非 EEXIST。
        #  語義上就是「檔還在」→ 這次沒搶到；呼叫端重試。真正的權限問題會在 Lock 逾時／doctor --probe-fs 現形。
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, sort_keys=True)
    return True


def read_claim(path) -> dict | None:
    """claim 內容；缺檔／空檔／半截（O_EXCL 建檔與寫 JSON 之間的窗，I-2）一律回 None＝壞 claim。"""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    if not text.strip():
        return None
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) else None


def release(path) -> bool:
    """刪認領檔；回 True＝這次刪掉了、False＝本來就不在（重複釋放、被接管後釋放都會發生，不算錯）。
    #! 回歸 review-6：Windows 上別的行程正開著這個檔（jobs／claim_owner 讀取中）unlink 會 PermissionError——退避重試，用盡才拋 FsBusy。"""
    path = Path(path)
    for i, backoff in enumerate(_REPLACE_BACKOFF_S):
        try:
            os.unlink(path)
            return True
        except FileNotFoundError:
            return False
        except PermissionError:
            if i == len(_REPLACE_BACKOFF_S) - 1:
                raise FsBusy(f"unlink 重試 {len(_REPLACE_BACKOFF_S)} 次仍被佔用：{path}")
            time.sleep(backoff)
    return False


# ── mtime 心跳 ──────────────────────────────────────────────────────────────
def touch(path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    os.utime(path, None)


def mtime(path) -> float | None:
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def is_stale(path, stale_s: float, now: float | None = None) -> bool:
    """`now - mtime > stale_s`；缺檔算 stale（沒有心跳＝死）。`now` 可注入給測試。"""
    m = mtime(path)
    if m is None:
        return True
    return ((time.time() if now is None else now) - m) > stale_s


def newest_mtime(dir_path, pattern: str = "*") -> float | None:
    """目錄下最新檔的 mtime（進度心跳來源，如 batches/<store>/results/）；沒有檔回 None。"""
    d = Path(dir_path)
    if not d.is_dir():
        return None
    ms = [m for m in (mtime(p) for p in d.glob(pattern) if p.is_file()) if m is not None]
    return max(ms) if ms else None


# ── 鎖 ──────────────────────────────────────────────────────────────────────
class Lock:
    """認領式鎖：`with Lock(path):`。拿不到就等；持鎖者的檔 mtime 老過 stale_s ＝ 死了 → 破鎖。

    #! 破鎖用 os.replace(path → path.broken.<pid>) 而不是 remove：兩個破鎖者同時動手時只有一個 rename 成功，
    #  另一個拿到 FileNotFoundError 重來——remove 會刪到對方剛建好的**新**鎖（I-3 的變體）。
    """

    def __init__(self, path, *, stale_s: float = 180.0, timeout_s: float = 90.0, payload: dict | None = None,
                 sleep=time.sleep, now=time.time):
        self.path = Path(path)
        self.stale_s, self.timeout_s = stale_s, timeout_s
        self.payload = payload
        self._sleep, self._now = sleep, now

    def __enter__(self):
        payload = self.payload or {"pid": os.getpid(), "at": now_iso()}
        t0 = self._now()
        while True:
            if try_claim(self.path, payload):
                return self
            if is_stale(self.path, self.stale_s, now=self._now()):
                broken = self.path.with_name(f"{self.path.name}.broken.{os.getpid()}.{random.randrange(16**4):04x}")
                try:
                    os.replace(self.path, broken)
                except (FileNotFoundError, PermissionError):
                    pass  # 別人先破了／正在寫——重來就好
            #! 回歸 review-5：破鎖分支以前 `continue` 跳過下面兩行——NAS 拒建檔（try_claim 回 False、檔又不存在）
            #  就變成 100% CPU 的無限忙迴圈。每一圈都要走到逾時檢查與 sleep。
            if self._now() - t0 > self.timeout_s:
                raise LockTimeout(f"{self.path} 佔用 >{self.timeout_s:.0f}s——查殭屍鎖或 NAS 權限")
            self._sleep(0.02 + random.random() * 0.03)

    def __exit__(self, *exc):
        release(self.path)


# ── 目錄清掃 ────────────────────────────────────────────────────────────────
def sweep_dirs(parent) -> list:
    """刪 parent 直屬的所有**子目錄**（不碰檔、不碰外面）；回刪掉的路徑。parent 不存在回 []。

    #! 回歸 I-1（2026-07-15）：中斷殘留的模擬工作目錄吃滿系統碟 → 連環 COM 例外。worker 啟動時整清。
    """
    parent = Path(parent)
    if not parent.is_dir():
        return []
    removed = []
    for child in parent.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
            removed.append(child)
    return removed
