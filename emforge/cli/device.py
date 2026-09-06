"""emforge/cli/device.py — 儀器層 CLI（M14）：fleet／device-state／device-describe／device-estop。

`fleet`＝機隊儀表板（狀態字典＋讀者推導的 offline；`--mcp-config` 吐 `.mcp.json` 片段，url 由 M15 填）。
`device-estop clear` 是**唯一**解除急停的路徑（要 `--confirm`）；MCP 只有 engage。
"""
import json

from .. import paths
from ..device import estop
from ..device.states import read_fleet
from .base import EXIT_ERROR, EXIT_OK, EXIT_REFUSED, add_root, depot_of, err, root_of


def _depot(args):
    return depot_of(args, root_of(args))


def cmd_fleet(args) -> int:
    depot = _depot(args)
    fleet = read_fleet(depot)
    if args.mcp_config:
        cfg = {"mcpServers": {f"emforge-{d['tag']}": {"type": "http", "url": d.get("url") or ""} for d in fleet}}
        print(json.dumps(cfg, ensure_ascii=False, indent=1))
        return EXIT_OK
    if not fleet:
        print("（無儀器：還沒有任何 worker 以儀器層啟動過）")
    else:
        print(f"{'tag':<6} {'state':<8} {'owner':<22} {'store':<34} {'profile':<16} {'done':>5} {'err':>4} {'last_result':<19} estop")
        for d in fleet:
            state = "offline" if d.get("offline") else d.get("state")
            es = (d.get("estop") or {}).get("scope") or ""
            print(f"{d['tag']:<6} {state:<8} {str(d.get('owner') or ''):<22} {str(d.get('store') or ''):<34} "
                  f"{str(d.get('profile') or ''):<16} {d.get('n_done', 0):>5} {d.get('n_error', 0):>4} "
                  f"{str(d.get('last_result_at') or ''):<19} {es}")
    fleet_es = depot.get_json(paths.estop_fleet()) if depot.exists(paths.estop_fleet()) else None
    if fleet_es:
        print(f"⚠ 全機 ESTOP 中：{fleet_es.get('by')}：{fleet_es.get('reason')}（{fleet_es.get('at')}）")
    return EXIT_OK


def _add_fleet(sub) -> None:
    s = sub.add_parser("fleet", help="機隊儀表板：每台儀器的狀態字典（offline 由心跳年齡推導）")
    add_root(s)
    s.add_argument("--mcp-config", action="store_true", help="吐 .mcp.json 片段（url 由 M15 的 MCP server 填）")
    s.set_defaults(fn=cmd_fleet)


def cmd_device_state(args) -> int:
    st = _depot(args).get_json(paths.device_state(args.tag))
    if not st:
        err(f"儀器 {args.tag} 尚無狀態字典")
        return EXIT_ERROR
    print(json.dumps(st, ensure_ascii=False, indent=1))
    return EXIT_OK


def _add_device_state(sub) -> None:
    s = sub.add_parser("device-state", help="印一台儀器的狀態字典")
    s.add_argument("tag")
    add_root(s)
    s.set_defaults(fn=cmd_device_state)


def cmd_device_describe(args) -> int:
    key = paths.device_reference_json(args.tag) if args.json else paths.device_reference_md(args.tag)
    data = _depot(args).get_bytes(key)
    if data is None:
        err(f"儀器 {args.tag} 尚無說明檔（儀器 open 成功後才會寫）")
        return EXIT_ERROR
    print(data.decode("utf-8"))
    return EXIT_OK


def _add_device_describe(sub) -> None:
    s = sub.add_parser("device-describe", help="印一台儀器的說明檔（reference.md；--json 印 reference.json）")
    s.add_argument("tag")
    add_root(s)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_device_describe)


def cmd_device_estop(args) -> int:
    root = root_of(args)
    depot = depot_of(args, root)
    target = "本機" if args.local else (f"單機 {args.tag}" if args.tag else "全機")
    if args.action == "engage":
        if not args.reason:
            raise ValueError("engage 要 --reason（會寫進急停檔與裝置日誌）")
        where = estop.engage_local(root, by=args.by, reason=args.reason) if args.local \
            else estop.engage(depot, args.tag, by=args.by, reason=args.reason)
        print(f"ESTOP 已按下（{target}）：{where}；儀器 open／simulate 硬擋、正在跑的那筆 abort、worker job 之間不撿")
        return EXIT_OK
    if not args.confirm:
        err(f"解除急停（{target}）要 --confirm：先確認機器已經安全（ansysedt 沒殘留、磁碟夠、人已離開）")
        return EXIT_REFUSED
    cleared = estop.clear_local(root) if args.local else estop.clear(depot, args.tag)
    print(f"ESTOP 已解除（{target}）" if cleared else f"（{target} 本來就沒有急停）")
    return EXIT_OK


def _add_device_estop(sub) -> None:
    s = sub.add_parser("device-estop", help="急停：engage [--tag T | --local] --by WHO --reason R；clear 要 --confirm（唯一解除路徑）")
    s.add_argument("action", choices=("engage", "clear"))
    add_root(s)
    s.add_argument("--tag", help="單機（不給＝全機）")
    s.add_argument("--local", action="store_true", help="本機層 <root>/ESTOP（NAS 斷線也擋得住）")
    s.add_argument("--by", default="cli")
    s.add_argument("--reason")
    s.add_argument("--confirm", action="store_true")
    s.set_defaults(fn=cmd_device_estop)


COMMANDS = {"fleet": _add_fleet, "device-state": _add_device_state, "device-describe": _add_device_describe,
            "device-estop": _add_device_estop}
