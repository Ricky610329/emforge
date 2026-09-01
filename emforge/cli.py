"""emforge/cli.py — 命令列入口。

慣例：子命令 kebab-case ↔ 函式 `cmd_<snake>`；每個子命令由 `_add_<snake>(sub)` 建 parser 並
`set_defaults(fn=cmd_<snake>)`；`COMMANDS` 是 kebab 名 → `_add_*` 的登錄表（tests 會逐一對帳）。

#! 回歸 I-8（2026-08-06）：安全閘的退出碼曾被管線尾端吃掉而靜默失效。這裡每個命令都回傳 int，
#  `main` 把任何例外轉成非零 exit code 並印到 stderr——**永不吞例外、永不回 0 當作沒事**。
"""
import argparse
import sys

from . import __version__
from ._version import describe


def cmd_version(args) -> int:
    print(f"emforge {__version__} ({describe()})")
    return 0


def _add_version(sub) -> None:
    s = sub.add_parser("version", help="印出版本與 git 戳（worker_ver 的成分）")
    s.set_defaults(fn=cmd_version)


COMMANDS = {
    "version": _add_version,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="emforge", description="昂貴模擬下的設計搜尋平台——迴圈裡沒有 LLM。")
    sub = p.add_subparsers(dest="command", required=True, metavar="<command>")
    for add in COMMANDS.values():
        add(sub)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args))
    except Exception as e:  # noqa: BLE001 — CLI 邊界：所有例外都要變成非零 exit code
        print(f"emforge: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
