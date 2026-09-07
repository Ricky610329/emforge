"""有狀態退火範例；平台只處理送件與量測，接受率、溫度、checkpoint 留在算法端。"""
import json
import math
import os
from pathlib import Path
import numpy as np
from emforge.client import Client

def save(state):
    temp = Path("checkpoint.tmp")
    temp.write_text(json.dumps(state), encoding="utf-8")
    temp.replace("checkpoint.json")

def initial(description, seed, resume):
    checkpoint = Path(resume) / "checkpoint.json" if resume else Path("checkpoint.json")
    if checkpoint.exists():
        return json.loads(checkpoint.read_text(encoding="utf-8"))
    rng = np.random.default_rng(seed)
    bits = rng.random(description["shape"]) > .5
    bits[np.asarray(description["fixed_on"], bool)] = True
    return {"step": 0, "bits": bits.astype(int).tolist(), "score": None, "rng": rng.bit_generator.state}

def main():
    params = json.loads(os.environ.get("EMFORGE_PARAMS", "{}"))
    client = Client(os.environ["EMFORGE_ENDPOINT"], os.environ["EMFORGE_PROFILE"],
                    os.environ["EMFORGE_ALGORITHM"], run_id=os.environ["EMFORGE_RUN_ID"],
                    spec=os.environ.get("EMFORGE_SPEC"))
    desc = client.description
    state = initial(desc, int(os.environ.get("EMFORGE_SEED", "0")), os.environ.get("EMFORGE_RESUME_FROM"))
    stop = Path(os.environ["EMFORGE_RUN_STOP"])
    fixed = np.asarray(desc["fixed_on"], bool)
    free = np.argwhere(~fixed)
    for step in range(state["step"], int(params.get("rounds", 100))):
        if stop.exists() or not len(free):
            break
        rng = np.random.default_rng()
        rng.bit_generator.state = state["rng"]
        bits = np.array(state["bits"], bool)
        cell = tuple(free[rng.integers(len(free))])
        bits[cell] = ~bits[cell]
        sid = client.submit([bits], request_id=f"round_{step}", tags=[f"step_{step}"])
        while not stop.exists():
            try:
                client.wait(sid, timeout_s=1, poll_s=.1)
                break
            except TimeoutError:
                continue
        if stop.exists():
            break
        records = client.results(sid)
        if not records:
            break  # 預算耗盡或送件拒絕；狀態可從 inbox 查
        score = records[0].score
        temperature = max(.001, float(params.get("temperature", 1))*float(params.get("cooling", .98))**step)
        accepted = score is not None and (state["score"] is None or score >= state["score"]
                     or rng.random() < math.exp((score-state["score"])/temperature))
        if accepted:
            state.update(bits=bits.astype(int).tolist(), score=score)
        state.update(step=step+1, rng=rng.bit_generator.state)
        save(state)
        client.log("anneal_step", step=step, accepted=accepted, temperature=temperature, score=score)
    save(state)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
