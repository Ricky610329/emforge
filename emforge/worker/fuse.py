"""emforge/worker/fuse.py — 連敗保險絲：純狀態機，零 I/O。

ok ──連 max_fail 敗──▶ cooldown（呼叫端：冷卻 cooldown_s、殺透重開）──▶ 重數
                       └─ 第 max_blowout 次 ──▶ blown（呼叫端：判死這批，寫 .fail）
成功一筆就歸零：COM 偶發錯（~15%）不該累積成判死。

#! 死亡判定三層（2026-07-15，216 教訓）：連敗＝熔斷→冷卻重開再試，循環用盡才判死；單次失敗永不判死。
"""


class Fuse:
    def __init__(self, max_fail: int = 5, cooldown_s: float = 600.0, max_blowout: int = 3):
        self.max_fail, self.cooldown_s, self.max_blowout = int(max_fail), float(cooldown_s), int(max_blowout)
        self.fails = 0
        self.blowouts = 0

    @property
    def blown(self) -> bool:
        return self.blowouts >= self.max_blowout

    def success(self) -> None:
        self.fails = 0

    def failure(self) -> str:
        """回 'ok'（繼續）／'cooldown'（冷卻＋重開）／'blown'（判死）。"""
        if self.blown:
            return "blown"
        self.fails += 1
        if self.fails < self.max_fail:
            return "ok"
        self.fails = 0
        self.blowouts += 1
        return "blown" if self.blown else "cooldown"
