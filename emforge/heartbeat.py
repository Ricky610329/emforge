"""emforge/heartbeat.py — 背景心跳執行緒：runtime（鎖）與儀器（狀態字典）共用。

每 interval_s 呼叫一次 `fn()`：回 `False`＝租約已經不在／不是自己的 → 立 `lost` 旗標，主迴圈下一圈看到就停（§12-16）；
拋例外＝不知道（NAS 短暫斷線）→ 吞掉、下一拍再試，**不**立 lost；回 True／None＝正常。
#! review-7：一個 tick 可能超過鎖的 stale 門檻（策略子行程逐個逾時），所以心跳不能只在 tick 開頭做。
"""
import threading


class Heartbeat(threading.Thread):
    def __init__(self, fn, interval_s: float, name: str = "emforge-heartbeat"):
        super().__init__(daemon=True, name=name)
        self._fn, self._interval = fn, float(interval_s)
        self._halt = threading.Event()      # 不叫 _stop：Thread 內部有同名方法
        self.lost = threading.Event()

    def run(self) -> None:
        while not self._halt.wait(self._interval):
            try:
                ok = self._fn()
            except Exception:  # noqa: BLE001 — 心跳失敗不能炸執行緒；持續失敗會讓租約 stale、被別人看到
                continue
            if ok is False:
                self.lost.set()

    def stop(self) -> None:
        self._halt.set()
        self.join(timeout=5)
