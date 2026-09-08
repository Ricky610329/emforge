"""emforge/strategy.py — 策略契約的執行端：載入、`COMPATIBLE` 檢查、Proposal 驗證、Context、子行程 propose、strategies.yaml。

策略＝一個 `.py`：`COMPATIBLE = {...}` + `propose(ctx) -> list[Proposal]`。這裡是它與 runtime 之間的全部介面。
runtime 一律用 `propose_in_subprocess`（策略例外／記憶體尖峰／卡死都關在子行程，I-4／I-5）；
`propose_in_process` 給子行程本身與 `emforge check-strategy` 用。
"""
import argparse
import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path

import numpy as np
import yaml

from . import db as dbm
from . import paths, profiles, priority
from .depot import open_depot
from .model import Context, Profile, Proposal, ProposalError

SHIPPED_DIR = Path(__file__).resolve().parent / "strategies"


class StrategyFailure(Exception):
    """載入／契約／子行程失敗。"""


class StrategyTimeout(Exception):
    """propose 超過逾時，子行程已殺。"""


class ConfigError(Exception):
    """strategies.yaml 不合規（不明鍵、重名、保留字、profile 不符）。"""


# ── 設定檔 ──────────────────────────────────────────────────────────────────
@dataclass
class RuntimeConfig:
    """數值只是佔位預設——架構文件刻意不定數值，跨域全部要重調。"""
    tick_s: int = 60
    background_prio: int = 9
    notarize_prio: int = 1
    repeat_n: int = 2
    noise_floor: float = 0.3
    k_min: int = 20
    quiet_s: int = 3600
    max_error_rate: float = 0.5
    propose_timeout_s: int = 600
    strategy_error_limit: int = 3


@dataclass
class StrategyConfig:
    name: str
    prio: int = 3
    batch: int = 1
    max_inflight: int = 1
    enabled: bool = True
    seed: int | None = None
    propose_timeout_s: int | None = None
    params: dict = field(default_factory=dict)
    kind: str = "propose"
    priority: str | None = None


@dataclass
class StrategiesFile:
    profile: str
    runtime: RuntimeConfig
    strategies: list


def _from_mapping(cls, d: dict, where: str):
    known = {f.name for f in fields(cls)}
    unknown = set(d) - known
    if unknown:
        raise ConfigError(f"{where}: 不明鍵 {sorted(unknown)}（可用：{sorted(known)}）")
    try:
        return cls(**d)
    except TypeError as e:
        raise ConfigError(f"{where}: {e}") from None


def load_strategies_yaml(path, profile: str | None = None) -> StrategiesFile:
    """從**本機路徑**讀 `strategies.yaml` 再解析。已抽象化的呼叫端（report）用 `parse_strategies_yaml(text)`；
    runtime 這一半在 M12d 一起遷。"""
    return parse_strategies_yaml(Path(path).read_text(encoding="utf-8"), profile)


def parse_strategies_yaml(text: str, profile: str | None = None) -> StrategiesFile:
    """解析 `strategies.yaml` 的文字。未知鍵、重名、保留字、名字不合規、profile 不符一律 ConfigError（不靜默）。"""
    raw = yaml.safe_load(text) or {}
    unknown = set(raw) - {"profile", "runtime", "strategies"}
    if unknown:
        raise ConfigError(f"頂層不明鍵 {sorted(unknown)}")
    if "profile" not in raw:
        raise ConfigError("缺 profile:（實例綁定的 sim_profile，雙邊宣告）")
    if profile is not None and raw["profile"] != profile:
        raise ConfigError(f"yaml 的 profile={raw['profile']!r} 與實例 profile={profile!r} 不符")
    rt = _from_mapping(RuntimeConfig, dict(raw.get("runtime") or {}), "runtime")
    out, seen = [], set()
    for i, s in enumerate(raw.get("strategies") or []):
        sc = _from_mapping(StrategyConfig, dict(s), f"strategies[{i}]")
        if not paths.is_valid_name(sc.name):
            raise ConfigError(f"strategies[{i}]: 策略名 {sc.name!r} 不合規（^[a-z][a-z0-9_]*$）")
        if sc.name in paths.RESERVED_STRATEGY_NAMES:
            raise ConfigError(f"strategies[{i}]: {sc.name!r} 是保留字，不能當策略名")
        if sc.name in seen:
            raise ConfigError(f"strategies[{i}]: 策略名重複：{sc.name}")
        seen.add(sc.name)
        if sc.kind not in ("propose", "inbox") or sc.batch < 1 or sc.max_inflight < 1:
            raise ConfigError("kind 必須為 propose/inbox，batch/max_inflight 必須大於零")
        try:
            sc.prio = priority.value(sc.priority, sc.prio)
        except ValueError as e:
            raise ConfigError(str(e)) from e
        sc.params = dict(sc.params or {})
        out.append(sc)
    return StrategiesFile(profile=raw["profile"], runtime=rt, strategies=out)


# ── 載入 ────────────────────────────────────────────────────────────────────
def resolve_strategy_path(root, name: str) -> Path:
    """使用者 `<root>/strategies/<name>.py` 優先，其次內建 `emforge/strategies/<name>.py`。"""
    if name in paths.RESERVED_STRATEGY_NAMES:
        raise StrategyFailure(f"{name!r} 是保留字，不能當策略名")
    if not paths.is_valid_name(name):
        raise StrategyFailure(f"策略名 {name!r} 不合規（^[a-z][a-z0-9_]*$）")
    user = paths.user_strategies_dir(root) / f"{name}.py"
    if user.exists():
        return user
    shipped = SHIPPED_DIR / f"{name}.py"
    if shipped.exists():
        return shipped
    raise StrategyFailure(f"找不到策略 {name!r}：{user} 或內建 {shipped}")


def load_strategy(path):
    """載入一個策略檔並檢查契約：`COMPATIBLE` 是非空字串集合、`propose` 可呼叫。"""
    path = Path(path)
    spec = importlib.util.spec_from_file_location(f"emforge_strategy_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:  # noqa: BLE001 — 策略碼是外來的，任何錯都包成 StrategyFailure
        raise StrategyFailure(f"{path.name}: 載入失敗：{type(e).__name__}: {e}") from e
    compat = getattr(mod, "COMPATIBLE", None)
    if (not isinstance(compat, (set, frozenset, list, tuple)) or not compat
            or not all(isinstance(c, str) for c in compat)):
        raise StrategyFailure(f"{path.name}: 必須宣告 COMPATIBLE = {{'<profile 名>', ...}} 或 {{'*'}}")
    mod.COMPATIBLE = set(compat)
    if not callable(getattr(mod, "propose", None)):
        raise StrategyFailure(f"{path.name}: 必須定義可呼叫的 propose(ctx)")
    return mod


def check_compatible(mod, profile_name: str) -> None:
    """雙邊宣告的策略端（I-6）：策略沒說懂這個 profile 就不准載進這個實例。"""
    if paths.COMPATIBLE_ANY in mod.COMPATIBLE or profile_name in mod.COMPATIBLE:
        return
    raise StrategyFailure(f"策略 COMPATIBLE={sorted(mod.COMPATIBLE)} 不含實例 profile {profile_name!r}")


# ── 驗證 ────────────────────────────────────────────────────────────────────
def validate_proposals(raw, profile: Profile, budget: int) -> list:
    """list of dict|Proposal → list[Proposal]：形狀、0/1、fixed_on、budget、禁用鍵。任一筆錯整批拒（策略要自己修）。"""
    if not isinstance(raw, (list, tuple)):
        raise ProposalError(f"propose 必須回傳 list，得到 {type(raw).__name__}")
    if len(raw) > budget:
        raise ProposalError(f"提案 {len(raw)} 筆超過 budget {budget}")
    out = []
    for i, item in enumerate(raw):
        p = Proposal.from_dict(item)
        for key in ("tag", "run_id"):
            value = getattr(p, key)
            if value is not None and (not isinstance(value, str) or not paths.is_valid_name(value)):
                raise ProposalError(f"{key} 名稱不合法：{value!r}")
        try:
            priority.value(p.priority)
        except ValueError as e:
            raise ProposalError(str(e)) from e
        if p.purpose is not None and not isinstance(p.purpose, str):
            raise ProposalError("purpose 必須為文字")
        pat = np.asarray(p.pattern)
        if pat.shape != profile.shape:
            raise ProposalError(f"#{i}: pattern shape {pat.shape} ≠ profile.shape {profile.shape}")
        if pat.dtype != bool:
            if not np.isin(pat, (0, 1)).all():
                raise ProposalError(f"#{i}: pattern 必須是 bool 或 0/1")
            pat = pat.astype(bool)
        if not pat[profile.fixed_on].all():
            raise ProposalError(f"#{i}: fixed_on 像素（饋墊）必須為 True")
        out.append(Proposal(pattern=pat, parent=p.parent, arm=p.arm, note=dict(p.note), tag=p.tag, run_id=p.run_id,
                            priority=p.priority, purpose=p.purpose))
    return out


# ── Context 與 propose ──────────────────────────────────────────────────────
def make_context(root, profile: Profile, strategy_name: str, *, budget: int, seed: int, tick: int,
                 params: dict | None, depot=None) -> Context:
    """`root`＝本機（策略 workdir）；`depot`＝共享資料庫（預設 FileDepot(root)）。"""
    #! 檢查 #11（2026-09-07）：綁 write_profile——策略拿到的 View 已不持 Database，這是第二道圍籬（refresh 寫索引也受守門）。
    view = dbm.Database(depot if depot is not None else root, write_profile=profile.name).view(profile.name, strategy=strategy_name)
    workdir = paths.strategy_workdir(root, profile.name, strategy_name)
    workdir.mkdir(parents=True, exist_ok=True)
    return Context(db=view, profile=profile, budget=int(budget), rng=np.random.default_rng(int(seed)),
                   workdir=workdir, tick=int(tick), params=dict(params or {}))


def propose_in_process(root, profile: Profile, name: str, *, budget: int, seed: int, tick: int, params: dict | None,
                       timeout_s: float | None = None, depot=None) -> list:
    """載入 → 相容 → 建 ctx → propose → 驗證。子行程與 `check-strategy` 用；runtime 預設用子行程版。
    `timeout_s` 只為與 propose_in_subprocess 同簽名（可互換注入），這裡不生效。"""
    mod = load_strategy(resolve_strategy_path(root, name))
    check_compatible(mod, profile.name)
    ctx = make_context(root, profile, name, budget=budget, seed=seed, tick=tick, params=params, depot=depot)
    return validate_proposals(mod.propose(ctx), profile, budget)


def _write_proposals(path: Path, props: list, shape: tuple) -> None:
    """子行程 → 父行程的交接檔（npz，無 pickle）。空提案也要落檔，父行程才分得出「回空」與「沒跑完」。"""
    pats = np.stack([p.pattern for p in props]) if props else np.zeros((0, *shape), bool)
    notes = [json.dumps(p.note, ensure_ascii=False) for p in props]
    np.savez(path, patterns=pats,
             priorities=np.array([p.priority or "" for p in props], dtype=str),
             purposes=np.array([p.purpose or "" for p in props], dtype=str),
             tags=np.array([p.tag or "" for p in props], dtype=str),
             run_ids=np.array([p.run_id or "" for p in props], dtype=str),
             parents=np.array([p.parent or "" for p in props], dtype="<U64"),
             arms=np.array([p.arm or "" for p in props], dtype="<U64"),
             notes=np.array(notes) if notes else np.zeros((0,), dtype="<U1"))


def _read_proposals(path: Path) -> list:
    with np.load(path, allow_pickle=False) as z:
        pats, parents, arms, notes = z["patterns"], z["parents"], z["arms"], z["notes"]
        return [Proposal(pattern=pats[i], parent=str(parents[i]) or None, arm=str(arms[i]) or None,
                         note=json.loads(str(notes[i])), tag=str(z["tags"][i]) or None,
                         run_id=str(z["run_ids"][i]) or None,
                         priority=(str(z["priorities"][i]) or None) if "priorities" in z else None,
                         purpose=(str(z["purposes"][i]) or None) if "purposes" in z else None) for i in range(len(pats))]


def propose_in_subprocess(root, profile: Profile, name: str, *, budget: int, seed: int, tick: int,
                          params: dict | None, timeout_s: float, depot=None) -> list:
    """在子行程跑 propose；逾時殺掉（StrategyTimeout）、非零退出包成 StrategyFailure（父行程永遠活著，I-4）。
    `depot` 以 spec 字串（`--depot`）交給子行程重開；`memory://` 子行程看不到——呼叫端（CLI run）要改用 in-process。"""
    workdir = paths.strategy_workdir(root, profile.name, name)
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "_proposals.npz"
    out.unlink(missing_ok=True)
    cmd = [sys.executable, "-X", "utf8", "-m", "emforge.strategy", "--root", str(root), "--profile", profile.name,
           "--strategy", name, "--budget", str(budget), "--seed", str(seed), "--tick", str(tick),
           "--params", json.dumps(params or {}, ensure_ascii=False), "--out", str(out)]
    if depot is not None:
        cmd += ["--depot", open_depot(depot).spec]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_s)
    except subprocess.TimeoutExpired:
        raise StrategyTimeout(f"策略 {name} propose 超過 {timeout_s}s，子行程已殺") from None
    if r.returncode != 0:
        tail = "\n".join((r.stderr or "").strip().splitlines()[-8:])
        raise StrategyFailure(f"策略 {name} 子行程失敗（rc={r.returncode}）：\n{tail}")
    if not out.exists():
        raise StrategyFailure(f"策略 {name} 子行程正常結束但沒有輸出 {out}")
    return validate_proposals(_read_proposals(out), profile, budget)


# ── 子行程入口 ──────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m emforge.strategy", description="策略子行程入口（runtime 內部用）")
    for flag in ("--root", "--profile", "--strategy", "--params", "--out"):
        ap.add_argument(flag, required=True)
    for flag in ("--budget", "--seed", "--tick"):
        ap.add_argument(flag, required=True, type=int)
    ap.add_argument("--depot", help="共享狀態後端 spec（file://…／memory://…）；預設 root")
    a = ap.parse_args(argv)
    root = Path(a.root)
    profiles.load_user_registry(root)
    profile = profiles.get_profile(a.profile)
    props = propose_in_process(root, profile, a.strategy, budget=a.budget, seed=a.seed, tick=a.tick,
                               params=json.loads(a.params), depot=open_depot(a.depot) if a.depot else None)
    _write_proposals(Path(a.out), props, profile.shape)
    return 0


if __name__ == "__main__":
    sys.exit(main())
