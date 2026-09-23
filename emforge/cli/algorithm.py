"""算法註冊、指定機器啟停及節點服務。"""
import json
import time
from pathlib import Path

from .platform import _connection, platform_of
from ..runner.node import Runner


def cmd_algorithm_register(args):
    root = Path(args.source).resolve()
    if not root.is_dir():
        raise ValueError("--source 必須為算法程式碼目錄")
    files = {}
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if not p.is_file() or p.is_symlink() or any(x in (".git", ".venv", "__pycache__") for x in rel.parts):
            continue
        try:
            files[rel.as_posix()] = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ValueError(f"{rel.as_posix()} 不是 UTF-8 文字檔——算法程式碼包只收文字；把資料檔移出 --source 目錄") from None
    version = platform_of(args).call("algorithm_register", name=args.name, files=files,
                                    entrypoint=args.entrypoint, requires=args.requires,
                                    checkpoint_schema=args.checkpoint_schema)
    print(version)
    return 0


def cmd_algorithm_start(args):
    value = platform_of(args).call("run_start", name=args.name, version=args.version, run_id=args.run_id,
                                  node=args.node, environment=args.environment, profile=args.profile,
                                  params=json.loads(args.params), seed=args.seed, budget=args.budget,
                                  spec=args.spec, resume_from=args.resume_from)
    print(json.dumps(value, ensure_ascii=False))
    return 0


def cmd_algorithm_stop(args):
    print(json.dumps(platform_of(args).call("run_stop", run_id=args.run_id), ensure_ascii=False))
    return 0


def cmd_algorithm_status(args):
    op = "run_status" if args.run_id else "run_list"
    params = {"run_id": args.run_id} if args.run_id else {}
    print(json.dumps(platform_of(args).call(op, **params), ensure_ascii=False))
    return 0


def cmd_algorithm_logs(args):
    print(json.dumps(platform_of(args).call("run_logs", run_id=args.run_id), ensure_ascii=False))
    return 0


def cmd_algorithm_worker(args):
    environments = {}
    for item in args.environment:
        name, sep, python = item.partition("=")
        if not sep or not name or not python:
            raise ValueError("--environment 格式是 name=Python完整路徑")
        environments[name] = python
    with Runner(args.work_root, args.endpoint, args.node, environments, max_runs=args.max_runs,
                stop_timeout_s=args.stop_timeout_s) as runner:
        return run_node_loop(runner, args.poll_s)


def run_node_loop(runner, poll_s: float, *, sleep=time.sleep) -> int:
    """節點主迴圈：瞬斷重試；認證失敗（PermissionError 也是 OSError）直接退出 2——以前被當暫時錯誤無限重試（I-34）。"""
    try:
        while True:
            try:
                runner.tick()
            except PermissionError as e:
                print(f"算法節點認證失敗，停止：{e}（檢查 EMFORGE_PLATFORM_TOKEN）", flush=True)
                return 2
            except (OSError, RuntimeError) as e:
                print(f"算法節點暫時無法更新：{e}", flush=True)
            sleep(poll_s)
    except KeyboardInterrupt:
        pass
    return 0


def _register(sub):
    p = sub.add_parser("algorithm-register", help="上傳固定版本文字程式碼包")
    _connection(p)
    p.add_argument("--name", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--entrypoint", default="main.py")
    p.add_argument("--requires", action="append", default=[])
    p.add_argument("--checkpoint-schema")
    p.set_defaults(fn=cmd_algorithm_register)


def _start(sub):
    p = sub.add_parser("algorithm-start", help="指定版本、環境與機器啟動算法")
    _connection(p)
    for flag in ("name", "version", "run-id", "node", "environment", "profile"):
        p.add_argument("--" + flag, required=True)
    p.add_argument("--params", default="{}")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--budget", type=int, default=100)
    p.add_argument("--spec")
    p.add_argument("--resume-from")
    p.set_defaults(fn=cmd_algorithm_start)


def _stop(sub):
    p = sub.add_parser("algorithm-stop", help="停止算法，已提交模擬繼續收件")
    _connection(p)
    p.add_argument("--run-id", required=True)
    p.set_defaults(fn=cmd_algorithm_stop)


def _status(sub):
    p = sub.add_parser("algorithm-status", help="算法執行狀態；離線不代表已結束")
    _connection(p)
    p.add_argument("--run-id")
    p.set_defaults(fn=cmd_algorithm_status)


def _logs(sub):
    p = sub.add_parser("algorithm-logs", help="算法私有 log 及 stdout 尾端")
    _connection(p)
    p.add_argument("--run-id", required=True)
    p.set_defaults(fn=cmd_algorithm_logs)


def _worker(sub):
    p = sub.add_parser("algorithm-worker", help="算法機啟停服務，主動連到平台")
    p.add_argument("--endpoint", required=True)
    p.add_argument("--node", required=True)
    p.add_argument("--work-root", required=True)
    p.add_argument("--environment", action="append", required=True)
    p.add_argument("--max-runs", type=int, default=1)
    p.add_argument("--poll-s", type=float, default=2)
    p.add_argument("--stop-timeout-s", type=float, default=30)
    p.set_defaults(fn=cmd_algorithm_worker)


COMMANDS = {"algorithm-register": _register, "algorithm-start": _start, "algorithm-stop": _stop,
            "algorithm-status": _status, "algorithm-logs": _logs, "algorithm-worker": _worker}
