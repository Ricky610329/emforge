"""emforge/cli/control.py — 操作 runtime／佇列：requeue／resume／stop／smoke。

resume 走 `control.json`（runtime 下個 tick 消費），不直接改 state.json——runtime 每 tick 會覆寫它。
"""
from .. import events, fs, paths
from ..queue import LiveClaim, Queue
from ..runtime.core import Runtime
from ..runtime.notarize import smoke_dispatch
from .base import EXIT_LIVE_CLAIM, EXIT_OK, add_root, err, root_of


def cmd_requeue(args) -> int:
    root = root_of(args)
    q = Queue(root)
    try:
        q.requeue(args.store)
    except LiveClaim as e:
        err(str(e))
        return EXIT_LIVE_CLAIM
    job = next(j for j in q.list() if j.store == args.store)
    events.emit(paths.events_jsonl(root, job.sim_profile), "batch_requeued", store=args.store, by=args.by)
    print(f"requeued {args.store}（claim／done／fail 一起清；進度在結果檔，會續跑）")
    return EXIT_OK


def _add_requeue(sub) -> None:
    s = sub.add_parser("requeue", help="重派一個 store（原子清 claim+done+fail；有新鮮 claim 拒）")
    s.add_argument("store")
    add_root(s)
    s.add_argument("--by", required=True)
    s.set_defaults(fn=cmd_requeue)


def cmd_resume(args) -> int:
    p = paths.control_json(root_of(args), args.profile)
    ctl = fs.read_json(p, default=None) or {}
    if args.strategy:
        ctl.setdefault("resume_strategies", [])
        if args.strategy not in ctl["resume_strategies"]:
            ctl["resume_strategies"].append(args.strategy)
    else:
        ctl["resume_profile"] = True
    ctl["by"] = args.by
    fs.atomic_write_json(p, ctl)
    what = f"策略 {args.strategy}" if args.strategy else "profile"
    print(f"已寫 {p}；runtime 下個 tick 消費（{what} 恢復）")
    return EXIT_OK


def _add_resume(sub) -> None:
    s = sub.add_parser("resume", help="恢復被暫停的策略（--strategy）或 profile（不給就是 profile）")
    add_root(s)
    s.add_argument("--profile", required=True)
    s.add_argument("--strategy")
    s.add_argument("--by", required=True)
    s.set_defaults(fn=cmd_resume)


def cmd_stop(args) -> int:
    root = root_of(args)
    if args.worker:
        q = Queue(root)
        (q.clear_stop if args.clear else q.request_stop)(args.machine_tag)
        target = f"worker{' ' + args.machine_tag if args.machine_tag else '（全機）'}"
    elif args.profile:
        p = paths.runtime_stop(root, args.profile)
        (fs.release if args.clear else fs.touch)(p)
        target = f"runtime {args.profile}"
    else:
        raise ValueError("stop 需要 --profile 或 --worker")
    print(f"{'清除' if args.clear else '建立'} STOP：{target}（在 job／tick 之間生效，不中斷單筆）")
    return EXIT_OK


def _add_stop(sub) -> None:
    s = sub.add_parser("stop", help="建 STOP 檔：--profile 停 runtime；--worker [--machine-tag] 停 worker；--clear 撤回")
    add_root(s)
    s.add_argument("--profile")
    s.add_argument("--worker", action="store_true")
    s.add_argument("--machine-tag")
    s.add_argument("--clear", action="store_true")
    s.set_defaults(fn=cmd_stop)


def cmd_smoke(args) -> int:
    rt = Runtime(root_of(args), args.profile)
    stores = smoke_dispatch(rt, args.id, n=args.n, machine=args.machine, by=args.by)
    rt.save_state()
    for s in stores:
        print(f"dispatched {s}（kind=repeat, strategy=cli:smoke, machine={args.machine or '任一'}）")
    return EXIT_OK


def _add_smoke(sub) -> None:
    s = sub.add_parser("smoke", help="對一個已量 id 派重測批（切機／換版本後驗同一儀器）")
    s.add_argument("id")
    add_root(s)
    s.add_argument("--profile", required=True)
    s.add_argument("--machine", help="釘給哪台（tag）")
    s.add_argument("--by", required=True)
    s.add_argument("--n", type=int, default=1)
    s.set_defaults(fn=cmd_smoke)


COMMANDS = {"requeue": _add_requeue, "resume": _add_resume, "stop": _add_stop, "smoke": _add_smoke}
