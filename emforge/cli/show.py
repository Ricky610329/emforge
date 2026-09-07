"""emforge/cli/show.py — 唯讀：status／events／pending／jobs／watch／report。AI 層與人讀狀態的入口。所有狀態經 `Depot`。"""
import json

from .. import ledger, paths, report
from ..queue import Queue
from .platform import platform_of
from .base import EXIT_OK, EXIT_REFUSED, add_root, depot_of, err, root_of


def _depot(args):
    return depot_of(args, root_of(args))


def cmd_status(args) -> int:
    st = _depot(args).get_json(paths.status_json(args.profile))
    print(json.dumps(st, ensure_ascii=False, indent=1) if st else f"尚無 status（profile {args.profile} 的 runtime 還沒跑過）")
    return EXIT_OK


def _add_status(sub) -> None:
    s = sub.add_parser("status", help="印 runtime 的 status.json")
    add_root(s)
    s.add_argument("--profile", required=True)
    s.set_defaults(fn=cmd_status)


def cmd_events(args) -> int:
    ev = _depot(args).read_log(paths.events_jsonl(args.profile))
    if args.event:
        ev = [e for e in ev if e.get("event") == args.event]
    for e in ev[-args.last:] if args.last else ev:
        rest = " ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in e.items() if k not in ("at", "event"))
        print(f"{e.get('at')}  {e.get('event'):<22} {rest}")
    return EXIT_OK


def _add_events(sub) -> None:
    s = sub.add_parser("events", help="印 events.jsonl（可過濾事件名、只看最後 N 筆）")
    add_root(s)
    s.add_argument("--profile", required=True)
    s.add_argument("--last", type=int, default=50)
    s.add_argument("--event", help="只印這個事件名")
    s.set_defaults(fn=cmd_events)


def cmd_pending(args) -> int:
    entries = ledger.Pending(_depot(args), args.profile).list()
    if not entries:
        print("（無待審）")
    for e in entries:
        print(f"{e.get('at')}  {e.get('id')}  conservative={e.get('conservative')}  spread={e.get('spread')}  scores={e.get('scores')}")
    return EXIT_OK


def _add_pending(sub) -> None:
    s = sub.add_parser("pending", help="印公證通過、等人審的候選")
    add_root(s)
    s.add_argument("--profile", required=True)
    s.set_defaults(fn=cmd_pending)


def cmd_jobs(args) -> int:
    q = Queue(_depot(args))
    for j in q.list():
        st = q.state(j.store)
        if st == "done" and not args.all:
            continue
        owner = f" ({q.claim_owner(j.store)})" if st == "claimed" else ""
        pin = f" pin={j.machine}" if j.machine else ""
        print(f"[prio {j.prio}] {j.store}: {st}{owner}{pin}  n={j.n} origin={j.origin}")
    return EXIT_OK


def _add_jobs(sub) -> None:
    s = sub.add_parser("jobs", help="印佇列（預設隱藏 done）")
    add_root(s)
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_jobs)


def cmd_watch(args) -> int:
    stores = [x for x in args.stores.split(",") if x]
    timeout_s = None if args.timeout_min is None else args.timeout_min * 60.0
    return Queue(_depot(args)).watch(stores, poll_s=args.poll_s, fail_grace_s=args.fail_grace_min * 60.0,
                                     timeout_s=timeout_s)


def _add_watch(sub) -> None:
    s = sub.add_parser("watch", help="blocking 等 stores 終態：0 全 done／1 有 fail／2 逾時")
    add_root(s)
    s.add_argument("--stores", required=True, help="逗號分隔")
    s.add_argument("--poll-s", type=float, default=30.0)
    s.add_argument("--fail-grace-min", type=float, default=20.0)
    s.add_argument("--timeout-min", type=float)
    s.set_defaults(fn=cmd_watch)


def cmd_report(args) -> int:
    try:
        service = platform_of(args)
        if args.evaluation:
            if len(args.profile) != 1:
                raise report.CrossProfileRefused("評估一次只能指定一個 profile")
            result = service.call("evaluate", profile=args.profile[0], kind=args.evaluation,
                                  strategy=args.strategy, run_id=args.run_id, spec=args.spec, since_tick=args.since_tick)
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(service.call("report", profiles=args.profile, cross_profile=args.cross_profile, k_min=args.k_min))
    except report.CrossProfileRefused as e:
        err(str(e))
        return EXIT_REFUSED
    return EXIT_OK


def _add_report(sub) -> None:
    s = sub.add_parser("report", help="每策略報表（blind 不夠只印數字；跨 profile 需 --cross-profile）")
    add_root(s)
    s.add_argument("--profile", action="append", required=True)
    s.add_argument("--cross-profile", action="store_true")
    s.add_argument("--k-min", type=int)
    s.add_argument("--endpoint")
    group = s.add_mutually_exclusive_group()
    for kind in ("curve", "calibration", "metrics"):
        group.add_argument("--" + kind, dest="evaluation", action="store_const", const=kind)
    for name in ("strategy", "run-id", "spec"):
        s.add_argument("--" + name)
    s.add_argument("--since-tick", type=int)
    s.set_defaults(fn=cmd_report)


COMMANDS = {"status": _add_status, "events": _add_events, "pending": _add_pending, "jobs": _add_jobs,
            "watch": _add_watch, "report": _add_report}
