"""emforge/fs.py — 檔案系統原語：`FileDepot`（depot/file.py）的實作細節，加上本機工作目錄清掃。

原語：`atomic_write_*`（tmp→replace）、`read_bytes/read_json`（退避重試）、`try_claim/read_claim/release`（O_EXCL 認領）、
`touch/mtime/is_stale/newest_mtime`（mtime 心跳）、`append_jsonl/read_jsonl`（**單寫者**檔）、`sweep_dirs`（I-1）。
協調狀態的呼叫端不直接用這裡——一律經 `Depot`（M12）；鎖＝`Depot.lock`（租約衍生），舊 `fs.Lock` 已退場。

假設（NTFS 本機與 SMB2+ 皆成立；舊系統以此在 NAS 上跑了兩個月）：
- `open(O_CREAT|O_EXCL)` 原子；同目錄 `os.replace` 原子；server mtime 可信且各機 NTP 同步（`doctor`／`selfcheck` 量偏移）。
- SMB 上多寫者 `O_APPEND` 交錯**無保證**——所以 `append_jsonl` 只給單寫者檔，多方匯總一律「一方一檔」。
"""
import json
import os
import random
import shutil
import time
from pathlib import Path


class LockTimeout(Exception):
    """`Depot.lock` 在 timeout_s 內拿不到（別人的鎖還新鮮）。函式庫層永不 SystemExit。"""


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
def _truncate_partial_tail(path: Path) -> None:
    """單寫者檔的尾巴若沒有換行＝上次 append 寫到一半死掉（NAS 斷線／斷電）；砍回最後一個換行，讓下一行不會黏上去變成中段壞行。"""
    with open(path, "r+b") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        if size == 0:
            return
        f.seek(size - 1)
        if f.read(1) == b"\n":
            return
        f.seek(0)
        data = f.read()
        f.truncate(data.rfind(b"\n") + 1)


def append_jsonl(path, obj) -> None:
    """append 一行。契約：**單寫者**檔（runtime 對自己的 events/pending、worker 對自己的 log/<tag>）。
    #! 回歸 I-23（2026-09-23）：尾行半截時先砍掉再 append，否則新行黏在半截後面＝永久的中段壞行、read_jsonl 永遠 FsCorrupt。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    if path.exists():
        _truncate_partial_tail(path)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()


def read_jsonl(path) -> list:
    """逐行 JSON → list；空行跳過；缺檔回 []；中段壞行拋 FsCorrupt。
    **最後一行且沒有換行**的半截（append 寫到一半死掉）跳過不拋（I-23）——append_jsonl 下次會把它砍掉。"""
    path = Path(path)
    if not path.exists():
        return []
    out = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                if not raw.endswith("\n"):
                    break                                   # 尾行半截：單寫者的殘骸，不是損壞
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
        f.flush()
        os.fsync(f.fileno())        # 斷電後不留 0 byte 鎖（I-2 本機版，2026-09-23）
    return True


def read_claim(path, *, retries: int = 6) -> dict | None:
    """claim 內容；缺檔／空檔／半截（O_EXCL 建檔與寫 JSON 之間的窗，I-2）一律回 None＝壞 claim。
    被佔用（別的行程正開著它）短暫退避重試（檢查 #5：讀不到不能當成「鎖沒了」），用盡仍回 None＝這次不知道。"""
    path = Path(path)
    text = None
    for i in range(max(1, retries)):
        try:
            text = path.read_text(encoding="utf-8")
            break
        except FileNotFoundError:
            return None
        except PermissionError:
            if i < retries - 1:
                time.sleep(0.02 * (i + 1))
    if text is None:
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
