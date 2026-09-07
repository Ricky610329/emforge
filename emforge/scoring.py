"""純數值評估：門檻先行；所有分數方向均為越高越好。"""
import math

def finite(value):
    try:
        return value is not None and math.isfinite(float(value))
    except (ValueError, TypeError, OverflowError):
        return False

def validate(spec):
    if not spec.axes or len(spec.axes) != len(spec.offsets):
        raise ValueError("axes 不得空白且須與 offsets 等長")
    if spec.aggregate not in ("min", "wsum"):
        raise ValueError("aggregate 必須是 min 或 wsum")
    if not all(finite(x) for x in spec.offsets):
        raise ValueError("offsets 必須是有限數值")
    if spec.weights and (len(spec.weights) != len(spec.axes) or not all(finite(x) for x in spec.weights)):
        raise ValueError("weights 必須與 axes 等長且為有限數值")
    if spec.aggregate == "wsum" and not spec.weights:
        raise ValueError("wsum 必須指定 weights")
    for gate in spec.gates:
        if len(gate) != 3 or not isinstance(gate[0], str) or gate[1] not in ("<=", ">=") or not finite(gate[2]):
            raise ValueError("gates 格式：(axis, <= 或 >=, 有限門檻)")

def passes(gates, measure):
    return all(finite(measure.get(a)) and
               (float(measure[a]) <= v if op == "<=" else float(measure[a]) >= v)
               for a, op, v in gates)

def score(spec, measure):
    if not passes(spec.gates, measure) or not all(finite(measure.get(a)) for a in spec.axes):
        return None
    values = [float(measure[a]) + o for a, o in zip(spec.axes, spec.offsets)]
    value = min(values) if spec.aggregate == "min" else sum(w*v for w, v in zip(spec.weights, values))
    return value if finite(value) else None
