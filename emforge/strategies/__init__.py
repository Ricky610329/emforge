"""內建策略。每個檔＝一個策略：`COMPATIBLE = {...}` + `propose(ctx) -> list[Proposal|dict]`。

這些是「幫使用者做得更好」的示範，不是平台的一部分：使用者在 `<root>/strategies/<name>.py` 放同名檔就蓋過。
"""
