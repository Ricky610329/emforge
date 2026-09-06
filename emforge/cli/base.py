"""emforge/cli/base.py — 子命令共用：exit code、`--root`／`--depot` 解析、stderr 訊息。

`--root`（或 `EMFORGE_ROOT`）＝本機程式碼／設定根：registry.py、strategies/、策略 workdir。
`--depot`（或 `EMFORGE_DEPOT`）＝共享協調狀態後端 spec（`file://<path>`／`memory://<name>`）；不給＝`FileDepot(root)`，
也就是今天的佈局、一個 byte 都不差。

#! 回歸 I-8（2026-08-06）：安全閘的退出碼曾被管線尾端吃掉而靜默失效——每個失敗都是非零 exit code＋stderr 原因。
exit code：0 成功／1 一般錯誤／2 拒絕（對帳不一致、跨 profile）／3 鎖被占或不在 pending／4 doctor 阻擋／
5 榜檔被手改／6 榜已存在（rescore 需 --force）／7 有新鮮 claim（requeue 需先 stop）。
"""
import os
import sys
from pathlib import Path

from ..depot import FileDepot, open_depot

EXIT_OK, EXIT_ERROR, EXIT_REFUSED, EXIT_LOCKED, EXIT_DOCTOR, EXIT_TAMPER, EXIT_EXISTS, EXIT_LIVE_CLAIM = range(8)


def root_of(args) -> Path:
    root = getattr(args, "root", None) or os.environ.get("EMFORGE_ROOT")
    if not root:
        raise ValueError("需要 --root 或環境變數 EMFORGE_ROOT")
    return Path(root)


def depot_of(args, root):
    """`--depot` > `EMFORGE_DEPOT` > `FileDepot(root)`。"""
    spec = getattr(args, "depot", None) or os.environ.get("EMFORGE_DEPOT")
    return open_depot(spec) if spec else FileDepot(root)


def add_root(parser) -> None:
    parser.add_argument("--root", help="本機程式碼／設定根：registry.py、strategies/（預設 EMFORGE_ROOT）")
    parser.add_argument("--depot", help="共享狀態後端 spec：file://<path>／memory://<name>（預設 EMFORGE_DEPOT，再預設＝root）")


def err(msg: str) -> None:
    print(f"emforge: {msg}", file=sys.stderr)
