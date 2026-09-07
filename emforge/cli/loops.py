"""emforge/cli/loops.py — 長跑行程：run（runtime 實例）／worker（正式機；`--serve` 同行程再起這台儀器的 MCP server）。"""
import argparse
import os

from .. import strategy
from ..device import mcp_server
from ..netid import local_tag
from ..runtime.core import Runtime, RuntimeLocked
from ..worker.loop import make_instrument, worker_loop
from .base import EXIT_ERROR, EXIT_LOCKED, add_root, depot_of, err, root_of


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


def _port(value) -> int:
    """`--port`／EMFORGE_MCP_PORT 的型別：錯值走 argparse 的 exit 2＋人話。
    #! 檢查 #14（2026-09-07）：以前建 parser 時就 int(環境變數)——EMFORGE_MCP_PORT="" 讓 version／--help／doctor 全吐 traceback，
    #  start_worker.cmd 誤報成「doctor 有阻擋條件」。現在字串預設交給 argparse，只有用到 --port 的子命令才轉型。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"MCP 埠要是整數，拿到 {value!r}（--port 或 EMFORGE_MCP_PORT）") from None


def add_serve_flags(parser) -> None:
    """`--host`／`--port`（預設 EMFORGE_MCP_HOST／EMFORGE_MCP_PORT）；token 一律讀 EMFORGE_DEVICE_TOKEN，不走旗標（別進 shell 歷史）。"""
    parser.add_argument("--host", default=os.environ.get("EMFORGE_MCP_HOST") or mcp_server.DEFAULT_HOST,
                        help="MCP 綁定位址（預設 EMFORGE_MCP_HOST 或 127.0.0.1；非 loopback 要 EMFORGE_DEVICE_TOKEN）")
    parser.add_argument("--port", type=_port, default=os.environ.get("EMFORGE_MCP_PORT") or mcp_server.DEFAULT_PORT,
                        help="MCP 埠（預設 EMFORGE_MCP_PORT 或 8765）")


def device_token() -> str | None:
    return os.environ.get("EMFORGE_DEVICE_TOKEN") or None


def cmd_worker(args) -> int:
    root = root_of(args)
    depot = depot_of(args, root)
    tag = args.machine_tag or local_tag()
    if not args.machine_tag and not os.environ.get("EMFORGE_MACHINE"):
        err(f"警告：沒給 --machine-tag 也沒設 EMFORGE_MACHINE，機器 tag 用 IP 末段「{tag}」——VPN／DHCP 換 IP 會換身分，"
            "單機急停／STOP／claim 都對不上（deploy.md §2 要每台 setx EMFORGE_MACHINE）")   # 檢查 #20
    loop_kw = dict(depot=depot, poll_s=args.poll_s, once=args.once, work_root=args.work_root, background_prio=args.bg_prio,
                   max_fail=args.max_fail, cooldown_s=args.cooldown_s, max_blowout=args.max_blowout,
                   retry_passes=args.retry_passes)
    if not args.serve:
        return worker_loop(root, tag, **loop_kw)
    #? MCP 與 worker 迴圈共用同一台儀器、同一行程：同機只能一個 HFSS 使用者；租約在行程內仲裁。
    mcp_server.check_bind(args.host, device_token())          # 先拒再起儀器
    inst = make_instrument(root, tag, depot=depot, work_root=args.work_root)
    inst.start()
    try:
        try:
            _, url = mcp_server.serve_in_thread(inst, host=args.host, port=args.port, secret=device_token())
        except RuntimeError as e:                        # 埠被佔／起不來：不跑 worker（與 check_bind 拒起同一種處置，檢查 #13）
            err(str(e))
            return EXIT_ERROR
        print(f"MCP server：{url}（auth={'bearer' if device_token() else '無（僅 loopback）'}）", flush=True)
        return worker_loop(root, tag, instrument=inst, **loop_kw)
    finally:
        inst.stop()


def _add_worker(sub) -> None:
    s = sub.add_parser("worker", help="在正式機跑 worker（認領 job → 模擬 → 結果檔）；--serve 同行程再起 MCP server")
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
    s.add_argument("--serve", action="store_true", help="同一行程再起這台儀器的 MCP server（daemon thread）")
    add_serve_flags(s)
    s.set_defaults(fn=cmd_worker)


COMMANDS = {"run": _add_run, "worker": _add_worker}
