"""top_k_flip — 從資料庫抓 top-k（保守值排序）、各翻 d 個非饋墊像素。鄰域開採的最小版。

params: k（起點數，預設 10）、d（翻幾格，預設 3）。資料庫空 → 回 []（交給 blind 打底，不硬湊）。
去重、饋墊檢查、seed 記錄、派工、評分、公證都不在這裡——那是 runtime 的事。
"""
import numpy as np

COMPATIBLE = {"*"}


def propose(ctx):
    k, d = int(ctx.params.get("k", 10)), int(ctx.params.get("d", 3))
    free = np.flatnonzero(~ctx.profile.fixed_on.reshape(-1))
    top = ctx.db.top(k)
    if not top:
        return []
    per = -(-ctx.budget // len(top))          # ceil：把 budget 填滿
    out = []
    for rec in top:
        for _ in range(per):
            q = rec.bits.copy().reshape(-1)
            pos = ctx.rng.choice(free, d, replace=False)
            q[pos] ^= True
            out.append(dict(pattern=q.reshape(ctx.profile.shape), parent=rec.id, tag=f"flip_k{k}", note={"d": d}))
    return out[:ctx.budget]
