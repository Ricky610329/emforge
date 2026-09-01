"""emforge/queue.py — 共用佇列：`queue/jobs.json`（全程持鎖）＋ `queue/state/<store>.{claim,done,fail}` ＋ STOP。

所有 worker（任何 profile）共用一條佇列；runtime 派工＝寫批＋加 job。認領是 O_EXCL 檔；
壞 claim（無主 >60 s）、陳 claim（claim 老**且**該批沒進度）、`.fail` 名單外的機器都可接管。
`requeue` 一次處理 claim＋done＋fail（I-2：分次手清會漏）。
"""
import time
from pathlib import Path

from . import fs, paths
from .batches import Batch
from .model import Job

#! 回歸 I-2（2026-08-03）：O_EXCL 建檔與寫 JSON 之間 worker 死了 → 空 claim。給 60 s 緩衝分辨「正在寫」與「死了」。
OWNERLESS_GRACE_S = 60.0
DEFAULT_STALE_S = 45 * 60.0


class DuplicateStore(Exception):
    pass


class MissingBatch(Exception):
    """job 指到的批不存在（先寫批再加 job）。"""


class MissingJob(Exception):
    pass


class LiveClaim(Exception):
    """有新鮮 claim 正在跑——先 stop 那台再 requeue。"""


class Queue:
    def __init__(self, root):
        self.root = Path(root)

    # ── jobs.json ───────────────────────────────────────────────────────
    def _read(self) -> list:
        return [Job.from_dict(d) for d in fs.read_json(paths.jobs_file(self.root), default=[])]

    def _write(self, jobs: list) -> None:
        fs.atomic_write_json(paths.jobs_file(self.root), [j.to_dict() for j in jobs])

    def add(self, job: Job) -> None:
        """持鎖讀-改-寫（I-3）。批必須已落地；store 名唯一。"""
        if not Batch(self.root, job.store).exists():
            raise MissingBatch(f"批 {job.store} 沒有 manifest——先寫批再加 job")
        job.at = job.at or fs.now_iso()
        with fs.Lock(paths.jobs_lock(self.root)):
            jobs = self._read()
            if any(j.store == job.store for j in jobs):
                raise DuplicateStore(f"{job.store} 已在佇列")
            jobs.append(job)
            self._write(jobs)

    def list(self) -> list:
        """prio 升冪（小者先）；同 prio 依加入順序。"""
        return sorted(self._read(), key=lambda j: j.prio)

    # ── 狀態 ────────────────────────────────────────────────────────────
    def state(self, store: str) -> str:
        if not any(j.store == store for j in self._read()):
            return "missing"
        if paths.done_file(self.root, store).exists():
            return "done"
        if paths.fail_file(self.root, store).exists():
            return "fail"
        if paths.claim_file(self.root, store).exists():
            return "claimed"
        return "queued"

    def claim_owner(self, store: str) -> str | None:
        c = fs.read_claim(paths.claim_file(self.root, store))
        return c.get("machine") if c else None

    def has_unclaimed_foreground(self, background_prio: int) -> bool:
        return any(j.prio < background_prio and self.state(j.store) == "queued" for j in self._read())

    # ── 認領 ────────────────────────────────────────────────────────────
    def pick(self, machine_tag: str, *, stale_s: float = DEFAULT_STALE_S, now: float | None = None):
        """依 prio 找第一個本機可認領的 job 並認領；沒有回 None。"""
        now = time.time() if now is None else now
        for job in self.list():
            if self._claim(job, machine_tag, stale_s, now):
                return job
        return None

    def _claim(self, job: Job, me: str, stale_s: float, now: float) -> bool:
        store = job.store
        if paths.done_file(self.root, store).exists():
            return False
        if job.machine and job.machine != me:          # 釘選＝tag 完全相等
            return False
        prior_fail = self._take_over_fail(store, me)
        if prior_fail is None:
            return False
        cp = paths.claim_file(self.root, store)
        if cp.exists():
            verdict = self._claim_verdict(cp, store, me, stale_s, now)
            if verdict == "mine":
                return True
            if verdict == "skip":
                return False
            fs.release(cp)                             # takeover：清掉再搶
        return fs.try_claim(cp, {"machine": me, "at": fs.now_iso(), "prior_fail": prior_fail})

    def _take_over_fail(self, store: str, me: str):
        """回 prior_fail 名單（沒 .fail ＝ []）；我在名單上、或接管競賽輸了 → None。"""
        fp = paths.fail_file(self.root, store)
        if not fp.exists():
            return []
        try:
            fail = fs.read_json(fp)
        except fs.FsCorrupt:
            return None
        machines = list((fail or {}).get("machines", []))
        if me in machines:
            return None
        try:
            fp.unlink()
        except FileNotFoundError:
            return None
        fs.release(paths.claim_file(self.root, store))
        return machines

    def _claim_verdict(self, cp: Path, store: str, me: str, stale_s: float, now: float) -> str:
        """'mine'（續跑自己的）／'skip'（別人活著）／'takeover'（壞 claim 過緩衝、或老 claim 且批無進度）。"""
        claim = fs.read_claim(cp)
        if claim is None:
            return "takeover" if fs.is_stale(cp, OWNERLESS_GRACE_S, now) else "skip"
        if claim.get("machine") == me:
            return "mine"
        newest = Batch(self.root, store).newest_result_mtime()
        progressed = newest is not None and (now - newest) < stale_s
        claim_fresh = not fs.is_stale(cp, stale_s, now)
        return "skip" if (progressed or claim_fresh) else "takeover"

    # ── 終態 ────────────────────────────────────────────────────────────
    def mark_done(self, store: str, machine_tag: str, *, n_done: int, n_error: int, error_ids: list) -> None:
        """「跑完」≠「全成功」：殘留 error 記在 done 裡。"""
        fs.atomic_write_json(paths.done_file(self.root, store),
                             {"machine": machine_tag, "at": fs.now_iso(), "n_done": n_done, "n_error": n_error,
                              "error_ids": list(error_ids)[:20]})
        fs.release(paths.claim_file(self.root, store))

    def mark_fail(self, store: str, machine_tag: str, reason: str) -> None:
        """本機對這批判死；名單累積，名單外的機器會接管。"""
        claim = fs.read_claim(paths.claim_file(self.root, store)) or {}
        machines = list(claim.get("prior_fail", [])) + [machine_tag]
        fs.atomic_write_json(paths.fail_file(self.root, store),
                             {"machines": machines, "last": str(reason), "at": fs.now_iso()})
        fs.release(paths.claim_file(self.root, store))

    def requeue(self, store: str, *, stale_s: float = DEFAULT_STALE_S) -> None:
        """一次清 claim＋done＋fail；有新鮮 claim（有人正在跑）→ LiveClaim。"""
        if not any(j.store == store for j in self._read()):
            raise MissingJob(f"{store} 不在佇列")
        cp = paths.claim_file(self.root, store)
        if cp.exists() and fs.read_claim(cp) is not None and not fs.is_stale(cp, stale_s):
            raise LiveClaim(f"{store} 有新鮮 claim（{self.claim_owner(store)}）——先 stop 那台")
        for f in (cp, paths.done_file(self.root, store), paths.fail_file(self.root, store)):
            fs.release(f)

    # ── STOP ────────────────────────────────────────────────────────────
    def stop_requested(self, machine_tag: str | None = None) -> bool:
        if paths.queue_stop(self.root).exists():
            return True
        return bool(machine_tag) and paths.queue_stop(self.root, machine_tag).exists()

    def request_stop(self, machine_tag: str | None = None) -> None:
        fs.touch(paths.queue_stop(self.root, machine_tag))

    def clear_stop(self, machine_tag: str | None = None) -> None:
        fs.release(paths.queue_stop(self.root, machine_tag))
