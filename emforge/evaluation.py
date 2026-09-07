"""固定 spec 的離線評估；共享／重測不增加獨立樣本數。"""
import numpy as np
from . import scoring

def samples(rows):
    seen, out = set(), []
    for row in sorted(rows, key=lambda r: (r.get("tick") or 0, r["id"])):
        if row.get("kind") == "repeat" or row["id"] in seen:
            continue
        seen.add(row["id"])
        out.append(row)
    return out

def efficiency_curve(rows):
    best, out = None, []
    for n, row in enumerate(samples(rows), 1):
        value = row.get("score")
        if scoring.finite(value):
            best = value if best is None else max(best, value)
        out.append({"n": n, "tick": row.get("tick"), "best": best})
    return out

def _ranks(values):
    vals = np.asarray(values, float)
    return np.array([np.count_nonzero(vals < v) + (np.count_nonzero(vals == v)-1)/2 for v in vals])

def calibration(rows):
    pairs = [(r.get("note", {}).get("pred"), r.get("score")) for r in samples(rows)]
    pairs = [(float(p), float(v)) for p, v in pairs if scoring.finite(p) and scoring.finite(v)]
    rho, mae = None, None
    if pairs:
        pred, actual = np.array(pairs).T
        mae = float(np.mean(np.abs(pred-actual)))
        x, y = _ranks(pred), _ranks(actual)
        if len(pairs) > 1 and np.std(x) > 0 and np.std(y) > 0:
            rho = float(np.corrcoef(x, y)[0, 1])
    return {"n": len(pairs), "mae": mae, "spearman": rho}

def metric_summary(rows, spec):
    out = {}
    directions = {a: op for a, op, _ in spec.gates}
    for axis in dict.fromkeys((*spec.axes, *directions)):
        vals = [float(r["measure"][axis]) for r in rows if scoring.finite(r.get("measure", {}).get(axis))]
        out[axis] = {"n": len(vals), "mean": float(np.mean(vals)) if vals else None,
                     "best": (min(vals) if directions.get(axis) == "<=" else max(vals)) if vals else None}
    return {"metrics": out, "gate_pass_rate": sum(scoring.passes(spec.gates, r.get("measure", {}))
                                                for r in rows)/len(rows) if rows else None}
