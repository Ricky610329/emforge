"""blind — 零演算法對照臂：均勻隨機 pattern（饋墊固定為 1），每筆標 arm="blind"。

#? 為什麼要有它（D8）：「策略 X 比較好」只在同批有零演算法臂時說得出口；report 在 blind 樣本不足時只印數字。
#  新域資料庫是空的時候，它也是自然的打底來源（冷啟動緩解，O-1）。
params: density（金屬密度，預設 0.5）。
"""
COMPATIBLE = {"*"}


def propose(ctx):
    density = float(ctx.params.get("density", 0.5))
    out = []
    for _ in range(ctx.budget):
        pat = ctx.rng.random(ctx.profile.shape) < density
        pat[ctx.profile.fixed_on] = True
        out.append(dict(pattern=pat, arm="blind"))
    return out
