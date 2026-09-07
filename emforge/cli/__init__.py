"""emforge.cli — 命令列入口（`emforge.cli:main`）。只做接線；命令本體分在：

  setup.py    version／init／check-strategy／doctor（準備與體檢）
  loops.py    run／worker（長跑行程）
  show.py     status／events／pending／jobs／watch／report（唯讀）
  verdict.py  promote／retire／rescore（人／AI 的裁決；唯一能寫榜）
  control.py  requeue／resume／stop／smoke／abandon（操作 runtime／佇列）
  device.py   fleet／device-state／device-describe／device-estop（儀器層，M14）
  base.py     exit code、--root／--depot、stderr

慣例：子命令 kebab-case ↔ `cmd_<snake>`；每個模組的 `COMMANDS` 是 kebab 名 → `_add_*`（tests 逐一對帳）。
所有命令回 int；`main` 把任何例外轉成 exit 1 並印到 stderr（I-8）。
"""
import argparse
import sys

from . import control, device, loops, setup, show, verdict, platform
from .base import EXIT_ERROR, err

COMMANDS = {**setup.COMMANDS, **loops.COMMANDS, **show.COMMANDS, **verdict.COMMANDS, **control.COMMANDS,
            **device.COMMANDS, **platform.COMMANDS}


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
        err(f"{type(e).__name__}: {e}")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
