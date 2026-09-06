"""emforge/cli/verdict.py — 人／AI 的裁決：promote（換王）／retire（凍結 profile）／rescore（換評估器建新榜）。

這是**唯一**能寫榜的路徑；runtime 只寫 pending（迴圈不加冕）。
"""
from .. import events, ledger, paths, profiles, specs
from ..db import Database
from .base import EXIT_EXISTS, EXIT_LOCKED, EXIT_OK, EXIT_TAMPER, add_root, err, root_of


def cmd_promote(args) -> int:
    root = root_of(args)
    profiles.load_user_registry(root)
    profile = profiles.get_profile(args.profile)
    spec = args.spec or profile.spec
    try:
        best = ledger.Ledger(root, profile.name, spec).promote(
            args.id, by=args.by, db=Database(root), pending=ledger.Pending(root, profile.name),
            force=args.force, note=args.note)
    except ledger.NotPending as e:
        err(str(e))
        return EXIT_LOCKED
    except ledger.LedgerTamper as e:
        err(str(e))
        return EXIT_TAMPER
    events.emit(root, paths.events_jsonl(profile.name), "promoted", id=args.id, spec=spec, by=args.by, note=args.note,
                score=best["score"], force=best["force"])
    print(f"promoted {args.id} → 榜 {profile.name}/{spec} score={best['score']} by={args.by}")
    return EXIT_OK


def _add_promote(sub) -> None:
    s = sub.add_parser("promote", help="換王（唯一能改榜的路徑；id 需在 pending，--force 可繞但會記錄）")
    s.add_argument("id")
    add_root(s)
    s.add_argument("--profile", required=True)
    s.add_argument("--by", required=True)
    s.add_argument("--spec")
    s.add_argument("--note", default="")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_promote)


def cmd_retire(args) -> int:
    root = root_of(args)
    marker = profiles.retire(root, args.profile, by=args.by)
    events.emit(root, paths.events_jsonl(args.profile), "retired", profile=args.profile, by=args.by)
    print(f"retired {args.profile}：拒收新工作，資料凍結保留（{marker}）")
    return EXIT_OK


def _add_retire(sub) -> None:
    s = sub.add_parser("retire", help="凍結一個 profile（拒收新工作，資料保留）")
    add_root(s)
    s.add_argument("--profile", required=True)
    s.add_argument("--by", required=True)
    s.set_defaults(fn=cmd_retire)


def cmd_rescore(args) -> int:
    root = root_of(args)
    profiles.load_user_registry(root)
    profile = profiles.get_profile(args.profile)
    spec = specs.get_spec(args.spec)
    try:
        out = ledger.rescore(root, profile, spec, Database(root), by=args.by, force=args.force)
    except ledger.LedgerExists as e:
        err(str(e))
        return EXIT_EXISTS
    events.emit(root, paths.events_jsonl(profile.name), "rescored", spec=spec.name, n=out["n"], by=args.by)
    best = out["best"] or {}
    print(f"rescored {profile.name}/{spec.name}: n={out['n']} best={best.get('id')} score={best.get('score')}")
    return EXIT_OK


def _add_rescore(sub) -> None:
    s = sub.add_parser("rescore", help="用另一個 spec 重算分數建新榜（零重量；榜已存在需 --force）")
    add_root(s)
    s.add_argument("--profile", required=True)
    s.add_argument("--spec", required=True)
    s.add_argument("--by", required=True)
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_rescore)


COMMANDS = {"promote": _add_promote, "retire": _add_retire, "rescore": _add_rescore}
