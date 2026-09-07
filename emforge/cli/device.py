"""emforge/cli/device.py — 儀器層 CLI（M14／M15）：fleet／device-state／device-describe／device-estop／device-serve。

`fleet`＝機隊儀表板（狀態字典＋讀者推導的 offline；`--mcp-config` 吐 `.mcp.json` 片段：url 來自各台狀態字典的 `url`、
header 用 `${EMFORGE_DEVICE_TOKEN}` 佔位）。`device-serve <tag>`＝不撿佇列、只服務 MCP 的獨立版（worker 平常用 `worker --serve`）。
`device-estop clear` 是**唯一**解除急停的路徑（要 `--confirm`）；MCP 只有 engage。
"""
import json
from pathlib import Path

from .. import paths
from ..device import estop, mcp_client, mcp_server
from ..device.states import read_fleet
from ..worker.loop import make_instrument
from .base import EXIT_ERROR, EXIT_OK, EXIT_REFUSED, add_root, depot_of, err, root_of
from .loops import add_serve_flags, device_token

MCP_CONFIG_HEADERS = {"Authorization": "Bearer ${EMFORGE_DEVICE_TOKEN}"}


def _depot(args):
    return depot_of(args, root_of(args))


def cmd_fleet(args) -> int:
    depot = _depot(args)
    fleet = read_fleet(depot)
    if args.mcp_config:
        cfg = {"mcpServers": {f"emforge-{d['tag']}": {"type": "http", "url": d.get("url") or "", "headers": dict(MCP_CONFIG_HEADERS)}
                              for d in fleet}}
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
    if not cleared:
        err(f"{target} 本來就沒有急停——什麼都沒清（要清別層請指定 --tag／--local；檢查 #21）")
        return EXIT_ERROR
    print(f"ESTOP 已解除（{target}）")
    return EXIT_OK


def _add_device_estop(sub) -> None:
    s = sub.add_parser("device-estop", help="急停：engage [--tag T | --local] --by WHO --reason R；clear 要 --confirm（唯一解除路徑）")
    s.add_argument("action", choices=("engage", "clear"))
    add_root(s)
    layer = s.add_mutually_exclusive_group()               # 檢查 #21：以前 --local 靜默蓋掉 --tag
    layer.add_argument("--tag", help="單機（不給＝全機）")
    layer.add_argument("--local", action="store_true", help="本機層 <root>/ESTOP（root 在本機碟時 NAS 斷線也擋得住）")
    s.add_argument("--by", default="cli")
    s.add_argument("--reason")
    s.add_argument("--confirm", action="store_true")
    s.set_defaults(fn=cmd_device_estop)


def cmd_device_serve(args) -> int:
    """只服務 MCP、不撿佇列（人／AI 單筆試量、或這台暫時不當 worker）。阻塞到 Ctrl-C。"""
    root = root_of(args)
    mcp_server.check_bind(args.host, device_token())          # 先拒再起儀器：非 loopback 無 token 不准
    inst = make_instrument(root, args.tag, depot=depot_of(args, root), work_root=args.work_root)
    inst.start()
    try:
        print(f"device-serve {args.tag}：{mcp_server.endpoint_url(args.host, args.port)}"
              f"（auth={'bearer' if device_token() else '無（僅 loopback）'}；不撿佇列）", flush=True)
        mcp_server.serve(inst, host=args.host, port=args.port, secret=device_token())
    except RuntimeError as e:                            # 埠被佔／起不來（serve 已把 uvicorn 的 SystemExit 轉掉，檢查 #13）
        err(str(e))
        return EXIT_ERROR
    finally:
        inst.stop()
    return EXIT_OK


def _add_device_serve(sub) -> None:
    s = sub.add_parser("device-serve", help="只起這台儀器的 MCP server（不撿佇列）；worker 平常用 `worker --serve`")
    s.add_argument("tag", help="機器 tag（狀態字典 devices/<tag>/）")
    add_root(s)
    s.add_argument("--work-root", help="本機工作目錄根（預設 EMFORGE_WORK）")
    add_serve_flags(s)
    s.set_defaults(fn=cmd_device_serve)


def _bits_arg(value: str) -> str:
    """`--bits 0101…` 或 `--bits @file`（檔內 0／1 可含換行；server 端 parse_bits 會清空白）。"""
    return Path(value[1:]).read_text(encoding="utf-8") if value.startswith("@") else value


def cmd_device_simulate(args) -> int:
    """對一台儀器的 MCP 下單跑一筆（兩段式：先印 token，再帶 --confirm）。token 讀 EMFORGE_DEVICE_TOKEN。"""
    call = {"profile": args.profile, "bits": _bits_arg(args.bits), "by": args.by}
    if args.confirm:
        call["confirm"] = args.confirm
    res = mcp_client.call_device(args.url, "device_simulate", call, token=device_token(), timeout_s=args.timeout_s)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if res.get("needs_confirm"):
        print(f"→ 確認無誤再跑一次，帶 --confirm {res['token']}（{int(res.get('preview', {}).get('timeout_s') or 0)} s 上限）")
    return EXIT_OK


def _add_device_simulate(sub) -> None:
    s = sub.add_parser("device-simulate", help="經 MCP 對一台儀器跑一筆（兩段式 confirm；結果不入 db）")
    s.add_argument("--url", required=True, help="該台的 MCP 端點，如 http://10.0.0.216:8765/mcp（fleet --mcp-config 有）")
    s.add_argument("--profile", required=True)
    s.add_argument("--bits", required=True, help="'0101…'（H×W 個 0／1）或 @檔案")
    s.add_argument("--confirm", help="第一次呼叫印出的 token")
    s.add_argument("--by", default="cli")
    s.add_argument("--timeout-s", type=float, default=3600.0, help="HTTP 讀逾時（模擬一筆 100–250 s）")
    s.set_defaults(fn=cmd_device_simulate)


COMMANDS = {"fleet": _add_fleet, "device-state": _add_device_state, "device-describe": _add_device_describe,
            "device-estop": _add_device_estop, "device-serve": _add_device_serve, "device-simulate": _add_device_simulate}
