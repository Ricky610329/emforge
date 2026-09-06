"""emforge/cli/loops.py — 長跑行程：run（runtime 實例）／worker（正式機）。"""
from .. import strategy
from ..netid import local_tag
from ..runtime.core import Runtime, RuntimeLocked
from ..worker.loop import worker_loop
from .base import EXIT_LOCKED, add_root, depot_of, err, root_of


def cmd_run(args) -> int:
    root = root_of(args)
    depot = depot_of(args, root)
    in_process = args.in_process
    if depot.spec.startswith("memory://") and not in_process:
        print(f"depot {depot.spec} 只存在本行程：策略改 in-process 跑（子行程看不到 memory://）", flush=True)
        in_process = True
    propose_fn = strategy.propose_in_process if in_process else None
    rt = Runtime(root, args.profile, depot=depot, propose_fn=propose_fn)
    try:
        return rt.run(once=args.once)
    except RuntimeLocked as e:
        err(str(e))
        return EXIT_LOCKED


def _add_run(sub) -> None:
    s = sub.add_parser("run", help="跑一個 runtime 實例（一實例一 profile）")
    add_root(s)
    s.add_argument("--profile", required=True)
    s.add_argument("--once", action="store_true", help="只跑一個 tick")
    s.add_argument("--in-process", action="store_true", help="策略在本行程跑（測試用；正式一律子行程）")
    s.set_defaults(fn=cmd_run)


def cmd_worker(args) -> int:
    root = root_of(args)
    return worker_loop(root, args.machine_tag or local_tag(), depot=depot_of(args, root), poll_s=args.poll_s,
                       once=args.once, work_root=args.work_root, background_prio=args.bg_prio, max_fail=args.max_fail,
                       cooldown_s=args.cooldown_s, max_blowout=args.max_blowout, retry_passes=args.retry_passes)


def _add_worker(sub) -> None:
    s = sub.add_parser("worker", help="在正式機跑 worker（認領 job → 模擬 → 結果檔）")
    add_root(s)
    s.add_argument("--machine-tag", help="預設 EMFORGE_MACHINE 或 IP 末段")
    s.add_argument("--poll-s", type=float, default=30.0)
    s.add_argument("--once", action="store_true")
    s.add_argument("--work-root", help="本機工作目錄根（預設 EMFORGE_WORK 或 %%LOCALAPPDATA%%\\emforge\\work）")
    s.add_argument("--bg-prio", type=int, default=9)
    s.add_argument("--max-fail", type=int, default=5)
    s.add_argument("--cooldown-s", type=float, default=600.0)
    s.add_argument("--max-blowout", type=int, default=3)
    s.add_argument("--retry-passes", type=int, default=2)
    s.set_defaults(fn=cmd_worker)


COMMANDS = {"run": _add_run, "worker": _add_worker}
