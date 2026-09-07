"""平台服務與收件 CLI。"""
import json
import os

import numpy as np

from .. import profiles
from ..client import Client
from ..platform.service import Platform
from ..platform.transport import RemotePlatform
from ..platform.http_server import make_server
from .base import add_root, root_of, depot_of


def platform_of(args):
    if args.endpoint:
        return RemotePlatform(args.endpoint)
    root = root_of(args)
    profiles.load_user_registry(root)
    return Platform(depot_of(args, root))


def cmd_platform_serve(args):
    root = root_of(args)
    profiles.load_user_registry(root)
    service = Platform(depot_of(args, root))
    server = make_server(service, host=args.host, port=args.port,
                         secret=os.environ.get("EMFORGE_PLATFORM_TOKEN"))
    print(f"平台 HTTP 已就緒：{args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def cmd_submit(args):
    platform = platform_of(args)
    endpoint = args.endpoint or platform.depot
    client = Client(endpoint, args.profile, args.name, run_id=args.run_id, spec=args.spec)
    with np.load(args.patterns, allow_pickle=False) as data:
        sid = client.submit(data["patterns"], request_id=args.request_id)
    print(sid)
    return 0


def cmd_inbox(args):
    print(json.dumps(platform_of(args).call("inbox_list", profile=args.profile), ensure_ascii=False))
    return 0


def _connection(parser):
    add_root(parser)
    parser.add_argument("--endpoint", help="平台 HTTP 位址；提供時不需 --root")


def _add_serve(sub):
    p = sub.add_parser("platform-serve", help="平台 HTTP 操作與遠端 Depot")
    add_root(p)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8766)
    p.set_defaults(fn=cmd_platform_serve)


def _add_submit(sub):
    p = sub.add_parser("submit", help="提交 npz patterns；回 sid")
    _connection(p)
    for flag in ("profile", "name", "run-id", "patterns"):
        p.add_argument("--" + flag, required=True)
    p.add_argument("--request-id")
    p.add_argument("--spec")
    p.set_defaults(fn=cmd_submit)


def _add_inbox(sub):
    p = sub.add_parser("inbox", help="列出送件、部分派工與完成狀態")
    _connection(p)
    p.add_argument("--profile", required=True)
    p.set_defaults(fn=cmd_inbox)


COMMANDS = {"platform-serve": _add_serve, "submit": _add_submit, "inbox": _add_inbox}


def cmd_platform_mcp(args):
    from ..platform.mcp_server import serve
    serve(platform_of(args), args.host, args.port, os.environ.get("EMFORGE_PLATFORM_TOKEN"))
    return 0


def _add_mcp(sub):
    p = sub.add_parser("platform-mcp", help="平台 MCP；可代理既有平台 HTTP")
    _connection(p)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8767)
    p.set_defaults(fn=cmd_platform_mcp)


COMMANDS["platform-mcp"] = _add_mcp
