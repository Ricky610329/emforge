"""程式碼執行前等待父節點完成行程隔離；父節點消失時收掉自己的 POSIX 行程群。"""
import os
import runpy
import signal
import sys
import threading


def _watch_parent():
    sys.stdin.read()
    if os.name != "nt":
        os.killpg(os.getpgrp(), signal.SIGKILL)


def main():
    if sys.stdin.readline().strip() != "go":
        return 1
    if os.name != "nt":
        threading.Thread(target=_watch_parent, daemon=True).start()
    entry = sys.argv[1]
    sys.path.insert(0, os.path.dirname(entry))
    runpy.run_path(entry, run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
