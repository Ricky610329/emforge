"""emforge/queue.py — 共用佇列：`queue/jobs.json`（全程持鎖）＋ `queue/state/<store>.{claim,done,fail}` ＋ STOP。

所有 worker（任何 profile）共用一條佇列；runtime 派工＝寫批＋加 job。認領＝`Depot` 租約（`claim`／`touch`／`release`／
`break_if_stale`）；壞 claim（無主 >60 s）、陳 claim（claim 老**且**該批沒進度）、`.fail` 名單外的機器都可接管。
`requeue` 一次處理 claim＋done＋fail（I-2：分次手清會漏）。

儲存一律走 `Depot`（jobs／done／fail＝doc、claim／jobs.lock＝lease、STOP＝空 doc），這個檔不認得檔案系統。
接管的仲裁是 **claim**，不是破鎖：破鎖可能多台同時 True（見 depot/file.py），但誰 claim 到誰算；`.fail` 的接管用 `delete()` 回值仲裁。
"""
import os
import socket
import time

from . import paths
from .batches import Batch
from .depot import FsCorrupt, open_depot
from .model import Job, now_iso

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
    def __init__(self, depot):
        self.depot = open_depot(depot)
        self._lock_owner = f"{socket.gethostname()}:{os.getpid()}"

    # ── jobs.json ───────────────────────────────────────────────────────
    def _read(self) -> list:
        return [Job.from_dict(d) for d in (self.depot.get_json(paths.jobs_file()) or [])]

    def _write(self, jobs: list) -> None:
        self.depot.put_json(paths.jobs_file(), [j.to_dict() for j in jobs])

    def add(self, job: Job) -> None:
        """持鎖讀-改-寫（I-3）。批必須已落地；store 名唯一。"""
        if not Batch(self.depot, job.store).exists():
            raise MissingBatch(f"批 {job.store} 沒有 manifest——先寫批再加 job")
        job.at = job.at or now_iso()
        with self.depot.lock(paths.jobs_lock(), owner=self._lock_owner):
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
        if self.depot.exists(paths.done_file(store)):
            return "done"
        if self.depot.exists(paths.fail_file(store)):
            return "fail"
        if self.depot.exists(paths.claim_file(store)):
            return "claimed"
        return "queued"

    def claim_owner(self, store: str) -> str | None:
        c = self.depot.owner(paths.claim_file(store))
        return c.get("owner") if c else None

    def release(self, store: str, machine_tag: str | None = None) -> bool:
        """放掉 claim（讓位用）；job 回到 queued，進度都在結果檔，任一機可續。
        給 machine_tag 只放自己的（被接管後不刪對方的）；不給＝強制（CLI／requeue）。"""
        return self.depot.release(paths.claim_file(store), owner=machine_tag)

    def touch_claim(self, store: str) -> None:
        """worker 每筆後心跳：claim 的 modified_at 才是真的活著（requeue／接管都看得到）；沒 claim 就不動、不建。"""
        self.depot.touch(paths.claim_file(store))

    def is_live(self, store: str, *, stale_s: float = DEFAULT_STALE_S, now: float | None = None) -> bool:
        """有人正在跑＝有主 claim 且（claim 新鮮 或 批有進度）。"""
        key = paths.claim_file(store)
        if self.depot.owner(key) is None:
            return False
        now = self.depot.now() if now is None else now
        return self._progressed(store, stale_s, now) or not self.depot.is_stale(key, stale_s, now)

    def _progressed(self, store: str, stale_s: float, now: float) -> bool:
        newest = Batch(self.depot, store).newest_result_at()
        return newest is not None and (now - newest) < stale_s

    def has_unclaimed_foreground(self, background_prio: int) -> bool:
        return any(j.prio < background_prio and self.state(j.store) == "queued" for j in self._read())

    # ── 認領 ────────────────────────────────────────────────────────────
    def pick(self, machine_tag: str, *, stale_s: float = DEFAULT_STALE_S, now: float | None = None):
        """依 prio 找第一個本機可認領的 job 並認領；沒有回 None。"""
        now = self.depot.now() if now is None else now
        for job in self.list():
            if self._claim(job, machine_tag, stale_s, now):
                return job
        return None

    def _claim(self, job: Job, me: str, stale_s: float, now: float) -> bool:
        """順序：done／釘選 → 我在 .fail 名單？ → claim 判定（自己的續跑／別人活著讓） → 接走 .fail（delete 仲裁）
        → 陳 claim 破鎖（沒破到＝別台先破、讓） → claim（O_EXCL 仲裁）。新鮮 claim 在任何情況下都不會被動到。"""
        store = job.store
        if self.depot.exists(paths.done_file(store)) or (job.machine and job.machine != me):
            return False
        try:
            fail = self.depot.get_json(paths.fail_file(store))
        except FsCorrupt:
            return False
        prior_fail = list((fail or {}).get("machines", []))
        if me in prior_fail:
            return False
        key = paths.claim_file(store)
        verdict, threshold = self._claim_verdict(key, store, me, stale_s, now)
        if verdict == "mine":
            return True
        if verdict == "skip":
            return False
        if fail is not None and not self.depot.delete(paths.fail_file(store)):
            return False                                   # 別台先接走 .fail → 輸了競賽（它會接著 claim）
        if verdict == "takeover" and not self.depot.break_if_stale(key, threshold, now):
            return False                                   # 別台先破了（它會接著 claim）→ 這輪讓
        return self.depot.claim(key, {"owner": me, "at": now_iso(), "prior_fail": prior_fail})

    def _claim_verdict(self, key: str, store: str, me: str, stale_s: float, now: float) -> tuple:
        """('free'|'mine'|'skip'|'takeover', 破鎖門檻)：沒 claim／續跑自己的／別人活著／壞 claim 過緩衝或老 claim 且批無進度。"""
        claim = self.depot.owner(key)
        if claim is None:
            if not self.depot.exists(key):
                return "free", None
            stale = self.depot.is_stale(key, OWNERLESS_GRACE_S, now)          # 空／半截 claim：正在寫 vs 死了
            return ("takeover", OWNERLESS_GRACE_S) if stale else ("skip", None)
        if claim.get("owner") == me:
            return "mine", None
        alive = self._progressed(store, stale_s, now) or not self.depot.is_stale(key, stale_s, now)
        return ("skip", None) if alive else ("takeover", stale_s)

    # ── 終態 ────────────────────────────────────────────────────────────
    def mark_done(self, store: str, machine_tag: str, *, n_done: int, n_error: int, error_ids: list) -> None:
        """「跑完」≠「全成功」：殘留 error 記在 done 裡。"""
        self.depot.put_json(paths.done_file(store),
                            {"machine": machine_tag, "at": now_iso(), "n_done": n_done, "n_error": n_error,
                             "error_ids": list(error_ids)[:20]})
        self.depot.release(paths.claim_file(store), owner=machine_tag)

    def mark_fail(self, store: str, machine_tag: str, reason: str) -> None:
        """本機對這批判死；名單累積，名單外的機器會接管。"""
        claim = self.depot.owner(paths.claim_file(store)) or {}
        machines = list(claim.get("prior_fail", [])) + [machine_tag]
        self.depot.put_json(paths.fail_file(store), {"machines": machines, "last": str(reason), "at": now_iso()})
        self.depot.release(paths.claim_file(store), owner=machine_tag)

    def requeue(self, store: str, *, stale_s: float = DEFAULT_STALE_S) -> None:
        """一次清 claim＋done＋fail；有新鮮 claim（有人正在跑）→ LiveClaim。"""
        if not any(j.store == store for j in self._read()):
            raise MissingJob(f"{store} 不在佇列")
        if self.is_live(store, stale_s=stale_s):           # claim 新鮮或批有進度都算活（review：claim 以前不心跳）
            raise LiveClaim(f"{store} 有人正在跑（{self.claim_owner(store)}）——先 stop 那台")
        for key in (paths.claim_file(store), paths.done_file(store), paths.fail_file(store)):
            self.depot.delete(key)

    # ── watch（blocking，給任何 harness 掛的收檔偵測） ────────────────────
    def watch(self, stores: list, *, poll_s: float = 30.0, fail_grace_s: float = 1200.0, timeout_s: float | None = None,
              sleep=time.sleep, out=print) -> int:
        """全 done → 0；任一 fail（過寬限仍無人接管）或不在佇列 → 1；超過 timeout_s → 2。"""
        t0 = self.depot.now()
        terminal: dict = {}
        while True:
            for s in stores:
                if s in terminal:
                    continue
                st = self.state(s)
                if st == "done":
                    out(f"{s} DONE {self.depot.get_json(paths.done_file(s)) or {}}")
                    terminal[s] = 0
                elif st == "missing":
                    out(f"{s} MISSING（不在佇列）")
                    terminal[s] = 1
                elif st == "fail":
                    m = self.depot.modified_at(paths.fail_file(s))
                    if m is None:
                        continue                          # state() 之後被接管刪掉了：下一輪再看（review）
                    age = self.depot.now() - m
                    if age >= fail_grace_s:
                        out(f"{s} FAIL（{age / 60:.0f} 分無人接管）：{self.depot.get_json(paths.fail_file(s)) or {}}")
                        terminal[s] = 1
            if len(terminal) == len(stores):
                return max(terminal.values(), default=0)
            if timeout_s is not None and self.depot.now() - t0 >= timeout_s:
                out(f"watch 逾時 {timeout_s:.0f}s：{sorted(set(stores) - set(terminal))} 尚未終態")
                return 2
            sleep(poll_s)

    # ── STOP ────────────────────────────────────────────────────────────
    def stop_requested(self, machine_tag: str | None = None) -> bool:
        if self.depot.exists(paths.queue_stop()):
            return True
        return bool(machine_tag) and self.depot.exists(paths.queue_stop(machine_tag))

    def request_stop(self, machine_tag: str | None = None) -> None:
        self.depot.put_bytes(paths.queue_stop(machine_tag), b"")

    def clear_stop(self, machine_tag: str | None = None) -> None:
        self.depot.delete(paths.queue_stop(machine_tag))
