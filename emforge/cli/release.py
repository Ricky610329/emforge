"""平台版本註冊、驗證、更新及 supervisor 命令。"""
import json
import sys
import time
from .. import paths
from ..release.store import ReleaseStore
from ..release.supervisor import Supervisor
from .base import add_root, root_of, depot_of

def cmd_release_stage(args):
    print(ReleaseStore(args.releases_root).stage(args.source, args.python))
    return 0

def cmd_release_validate(args):
    result = ReleaseStore(args.releases_root).validate(args.version, timeout_s=args.timeout_s)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["valid"] else 1

def cmd_release_apply(args):
    store = ReleaseStore(args.releases_root)
    if args.source:
        version = store.stage(args.source, args.python)
        result = store.validate(version, timeout_s=args.timeout_s)
        if not result["valid"]:
            print(json.dumps(result, ensure_ascii=False))
            return 1
    else:
        version = args.version
    print(json.dumps(store.request(version), ensure_ascii=False))
    return 0

def cmd_release_status(args):
    store = ReleaseStore(args.releases_root)
    print(json.dumps({"state": store.depot.get_json(paths.service_key("state")),
                      "request": store.depot.get_json(paths.service_key("request"))}, ensure_ascii=False))
    return 0

def cmd_platform_service(args):
    root = root_of(args)
    with Supervisor(args.releases_root, root, args.profile, depot=depot_of(args, root),
                    host=args.host, port=args.port) as supervisor:
        try:
            while True:
                supervisor.tick()
                time.sleep(args.poll_s)
        except KeyboardInterrupt:
            pass
    return 0

def _base(sub, name, help):
    p = sub.add_parser(name, help=help)
    p.add_argument("--releases-root", required=True)
    return p

def _stage(sub):
    p = _base(sub, "release-stage", "凍結候選程式碼；不修改運行版本")
    p.add_argument("--source", required=True)
    p.add_argument("--python", default=sys.executable)
    p.set_defaults(fn=cmd_release_stage)

def _validate(sub):
    p = _base(sub, "release-validate", "在隔離快照跑測試、pyflakes 與 fake 探針")
    p.add_argument("--version", required=True)
    p.add_argument("--timeout-s", type=float, default=600)
    p.set_defaults(fn=cmd_release_validate)

def _apply(sub):
    p = _base(sub, "release-apply", "驗證後要求 supervisor 切換；可由 agent 自動呼叫")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--version")
    group.add_argument("--source")
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--timeout-s", type=float, default=600)
    p.set_defaults(fn=cmd_release_apply)

def _status(sub):
    p = _base(sub, "release-status", "查目前版本、更新階段與回退原因")
    p.set_defaults(fn=cmd_release_status)

def _service(sub):
    p = _base(sub, "platform-service", "穩定 supervisor 管理 HTTP 與單 profile runtime")
    add_root(p)
    p.add_argument("--profile", required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--poll-s", type=float, default=.5)
    p.set_defaults(fn=cmd_platform_service)

COMMANDS = {"release-stage": _stage, "release-validate": _validate, "release-apply": _apply,
            "release-status": _status, "platform-service": _service}
