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

    def _p(self, key: str) -> Path:
        #! M12b 墊片：paths 已回 depot key，這個模組還沒遷（M12c）——先在這裡貼回本機路徑。
        return self.root / key

    # ── jobs.json ───────────────────────────────────────────────────────
    def _read(self) -> list:
        return [Job.from_dict(d) for d in fs.read_json(self._p(paths.jobs_file()), default=[])]

    def _write(self, jobs: list) -> None:
        fs.atomic_write_json(self._p(paths.jobs_file()), [j.to_dict() for j in jobs])

    def add(self, job: Job) -> None:
        """持鎖讀-改-寫（I-3）。批必須已落地；store 名唯一。"""
        if not Batch(self.root, job.store).exists():
            raise MissingBatch(f"批 {job.store} 沒有 manifest——先寫批再加 job")
        job.at = job.at or fs.now_iso()
        with fs.Lock(self._p(paths.jobs_lock())):
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
        if self._p(paths.done_file(store)).exists():
            return "done"
        if self._p(paths.fail_file(store)).exists():
            return "fail"
        if self._p(paths.claim_file(store)).exists():
            return "claimed"
        return "queued"

    def claim_owner(self, store: str) -> str | None:
        c = fs.read_claim(self._p(paths.claim_file(store)))
        return c.get("machine") if c else None

    def release(self, store: str) -> None:
        """放掉 claim（讓位用）；job 回到 queued，進度都在結果檔，任一機可續。"""
        fs.release(self._p(paths.claim_file(store)))

    def touch_claim(self, store: str) -> None:
        """worker 每筆後心跳：claim mtime 才是真的活著（requeue／接管都看得到）；沒 claim 就不動。"""
        cp = self._p(paths.claim_file(store))
        if cp.exists():
            fs.touch(cp)

    def is_live(self, store: str, *, stale_s: float = DEFAULT_STALE_S, now: float | None = None) -> bool:
        """有人正在跑＝有主 claim 且（claim 新鮮 或 批有進度）。"""
        cp = self._p(paths.claim_file(store))
        if not cp.exists() or fs.read_claim(cp) is None:
            return False
        now = time.time() if now is None else now
        newest = Batch(self.root, store).newest_result_at()
        progressed = newest is not None and (now - newest) < stale_s
        return progressed or not fs.is_stale(cp, stale_s, now)

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
        if self._p(paths.done_file(store)).exists():
            return False
        if job.machine and job.machine != me:          # 釘選＝tag 完全相等
            return False
        prior_fail = self._take_over_fail(store, me)
        if prior_fail is None:
            return False
        cp = self._p(paths.claim_file(store))
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
        fp = self._p(paths.fail_file(store))
        if not fp.exists():
            return []
        try:
            fail = fs.read_json(fp, default=None)      # exists() 之後被別台 unlink（review-6）→ None＝輸了競賽
        except fs.FsCorrupt:
            return None
        if fail is None:
            return None
        machines = list(fail.get("machines", []))
        if me in machines:
            return None
        try:
            fp.unlink()
        except FileNotFoundError:
            return None
        fs.release(self._p(paths.claim_file(store)))
        return machines

    def _claim_verdict(self, cp: Path, store: str, me: str, stale_s: float, now: float) -> str:
        """'mine'（續跑自己的）／'skip'（別人活著）／'takeover'（壞 claim 過緩衝、或老 claim 且批無進度）。"""
        claim = fs.read_claim(cp)
        if claim is None:
            return "takeover" if fs.is_stale(cp, OWNERLESS_GRACE_S, now) else "skip"
        if claim.get("machine") == me:
            return "mine"
        newest = Batch(self.root, store).newest_result_at()
        progressed = newest is not None and (now - newest) < stale_s
        claim_fresh = not fs.is_stale(cp, stale_s, now)
        return "skip" if (progressed or claim_fresh) else "takeover"

    # ── 終態 ────────────────────────────────────────────────────────────
    def mark_done(self, store: str, machine_tag: str, *, n_done: int, n_error: int, error_ids: list) -> None:
        """「跑完」≠「全成功」：殘留 error 記在 done 裡。"""
        fs.atomic_write_json(self._p(paths.done_file(store)),
                             {"machine": machine_tag, "at": fs.now_iso(), "n_done": n_done, "n_error": n_error,
                              "error_ids": list(error_ids)[:20]})
        fs.release(self._p(paths.claim_file(store)))

    def mark_fail(self, store: str, machine_tag: str, reason: str) -> None:
        """本機對這批判死；名單累積，名單外的機器會接管。"""
        claim = fs.read_claim(self._p(paths.claim_file(store))) or {}
        machines = list(claim.get("prior_fail", [])) + [machine_tag]
        fs.atomic_write_json(self._p(paths.fail_file(store)),
                             {"machines": machines, "last": str(reason), "at": fs.now_iso()})
        fs.release(self._p(paths.claim_file(store)))

    def requeue(self, store: str, *, stale_s: float = DEFAULT_STALE_S) -> None:
        """一次清 claim＋done＋fail；有新鮮 claim（有人正在跑）→ LiveClaim。"""
        if not any(j.store == store for j in self._read()):
            raise MissingJob(f"{store} 不在佇列")
        cp = self._p(paths.claim_file(store))
        if self.is_live(store, stale_s=stale_s):           # claim 新鮮或批有進度都算活（review：claim 以前不心跳）
            raise LiveClaim(f"{store} 有人正在跑（{self.claim_owner(store)}）——先 stop 那台")
        for f in (cp, self._p(paths.done_file(store)), self._p(paths.fail_file(store))):
            fs.release(f)

    # ── watch（blocking，給任何 harness 掛的收檔偵測） ────────────────────
    def watch(self, stores: list, *, poll_s: float = 30.0, fail_grace_s: float = 1200.0, timeout_s: float | None = None,
              sleep=time.sleep, out=print) -> int:
        """全 done → 0；任一 fail（過寬限仍無人接管）或不在佇列 → 1；超過 timeout_s → 2。"""
        t0 = time.time()
        terminal: dict = {}
        while True:
            for s in stores:
                if s in terminal:
                    continue
                st = self.state(s)
                if st == "done":
                    out(f"{s} DONE {fs.read_json(self._p(paths.done_file(s)), default={})}")
                    terminal[s] = 0
                elif st == "missing":
                    out(f"{s} MISSING（不在佇列）")
                    terminal[s] = 1
                elif st == "fail":
                    m = fs.mtime(self._p(paths.fail_file(s)))
                    if m is None:
                        continue                          # state() 之後被接管刪掉了：下一輪再看（review）
                    age = time.time() - m
                    if age >= fail_grace_s:
                        out(f"{s} FAIL（{age / 60:.0f} 分無人接管）：{fs.read_json(self._p(paths.fail_file(s)), default={})}")
                        terminal[s] = 1
            if len(terminal) == len(stores):
                return max(terminal.values(), default=0)
            if timeout_s is not None and time.time() - t0 >= timeout_s:
                out(f"watch 逾時 {timeout_s:.0f}s：{sorted(set(stores) - set(terminal))} 尚未終態")
                return 2
            sleep(poll_s)

    # ── STOP ────────────────────────────────────────────────────────────
    def stop_requested(self, machine_tag: str | None = None) -> bool:
        if self._p(paths.queue_stop()).exists():
            return True
        return bool(machine_tag) and self._p(paths.queue_stop(machine_tag)).exists()

    def request_stop(self, machine_tag: str | None = None) -> None:
        fs.touch(self._p(paths.queue_stop(machine_tag)))

    def clear_stop(self, machine_tag: str | None = None) -> None:
        fs.release(self._p(paths.queue_stop(machine_tag)))
