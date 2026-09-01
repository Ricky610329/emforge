"""emforge/cli/setup.py — 準備與體檢：version／init／check-strategy／doctor／import-legacy。"""
import json
import os

from .. import __version__, doctor, paths, profiles, strategy
from .._version import describe
from .base import EXIT_OK, add_root, root_of

REGISTRY_TEMPLATE = '''"""<root>/registry.py — 這個根目錄的模擬庫／評估器註冊表（append-only；改內容＝換名字）。
runtime 與 worker 啟動都會執行這個檔，所以兩邊看到同一套 profile／spec。
範例：
    from emforge.adapters.antenna.profiles import register_all
    register_all()
或自己寫：
    from emforge import model, profiles, specs
    specs.register_measure("my_m", my_measure_fn, labels=("S11",), targets={...})
    specs.register_spec(model.Spec(name="my_v1", labels=("S11",), measure="my_m", axes=("m1",), offsets=(0.0,)))
    profiles.register_profile(model.Profile(name="my_p01", simulator="my_pkg.sim:MySim", geom_ver="p01", kwargs={},
        shape=(25, 25), labels=("S11",), n_points=17, fixed_on=..., measure="my_m", spec="my_v1", timeout_s=900))
"""
'''

YAML_TEMPLATE = """profile: {profile}
runtime: {{tick_s: 60, background_prio: 9, notarize_prio: 1, repeat_n: 2, noise_floor: 0.3, k_min: 20,
          quiet_s: 3600, max_error_rate: 0.5, propose_timeout_s: 600, strategy_error_limit: 3}}
strategies:
  - {{name: blind, prio: 9, batch: 20}}
"""


def cmd_version(args) -> int:
    print(f"emforge {__version__} ({describe()})")
    return EXIT_OK


def _add_version(sub) -> None:
    sub.add_parser("version", help="印出版本與 git 戳（worker_ver 的成分）").set_defaults(fn=cmd_version)


def cmd_init(args) -> int:
    root = root_of(args)
    for d in paths.layout_dirs(root):
        d.mkdir(parents=True, exist_ok=True)
    targets = [(paths.registry_py(root), REGISTRY_TEMPLATE)]
    if args.profile:
        targets.append((paths.strategies_yaml(root, args.profile), YAML_TEMPLATE.format(profile=args.profile)))
    for path, text in targets:
        if path.exists():
            print(f"已存在，不覆寫：{path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"建立：{path}")
    print(f"root 佈局就緒：{root}")
    return EXIT_OK


def _add_init(sub) -> None:
    s = sub.add_parser("init", help="建立 root 佈局與範本（registry.py、strategies.yaml）")
    add_root(s)
    s.add_argument("--profile", help="同時給這個 profile 一份 strategies.yaml 範本")
    s.set_defaults(fn=cmd_init)


def cmd_check_strategy(args) -> int:
    root = root_of(args)
    profiles.load_user_registry(root)
    profile = profiles.get_profile(args.profile)
    props = strategy.propose_in_process(root, profile, args.strategy, budget=args.budget, seed=args.seed,
                                        tick=args.tick, params=json.loads(args.params))
    print(f"{args.strategy}: {len(props)} 筆提案（budget {args.budget}, seed {args.seed}, tick {args.tick}）")
    return EXIT_OK


def _add_check_strategy(sub) -> None:
    s = sub.add_parser("check-strategy", help="在本行程試跑一個策略的 propose（不派工）")
    add_root(s)
    s.add_argument("--profile", required=True)
    s.add_argument("--strategy", required=True)
    s.add_argument("--budget", type=int, default=5)
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--tick", type=int, default=0)
    s.add_argument("--params", default="{}", help="JSON")
    s.set_defaults(fn=cmd_check_strategy)


def cmd_doctor(args) -> int:
    return doctor.run(root_of(args), hfss=args.hfss)


def _add_doctor(sub) -> None:
    s = sub.add_parser("doctor", help="機器體檢（版本、root 可寫、磁碟、HFSS 行程）；非零就別起 worker")
    add_root(s)
    s.add_argument("--hfss", action="store_true")
    s.set_defaults(fn=cmd_doctor)


def cmd_import_legacy(args) -> int:
    from ..legacy.antenna_import import import_legacy
    out_root = args.out or os.environ.get("EMFORGE_ROOT")
    if not out_root:
        raise ValueError("需要 --out 或環境變數 EMFORGE_ROOT")
    overrides = dict(kv.split("=", 1) for kv in (args.map or []))
    _, rc = import_legacy(args.root, out_root, stores=[s for s in args.stores.split(",") if s], profile=args.profile,
                          overrides=overrides, include_errors=args.include_errors, verify=args.verify,
                          dry_run=args.dry_run, force=args.force, no_rad=args.no_rad)
    return rc


def _add_import_legacy(sub) -> None:
    s = sub.add_parser("import-legacy", help="舊 NAS（DATASET_PATH）資料匯入 db/<profile>/（只讀舊樹；--verify 對回舊尺）")
    s.add_argument("--root", required=True, help="舊 DATASET_PATH 或本機鏡像")
    s.add_argument("--out", help="emforge 根目錄（預設 EMFORGE_ROOT）")
    s.add_argument("--stores", required=True, help="逗號分隔 glob，如 dedust_*,handoff_*,harvest_*")
    s.add_argument("--profile", default="auto", help="auto 或只匯入映射到這個 profile 的 store")
    s.add_argument("--map", action="append", help="store=profile 強制映射（可重複）")
    s.add_argument("--include-errors", action="store_true")
    s.add_argument("--verify", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--force", action="store_true")
    s.add_argument("--no-rad", action="store_true")
    s.set_defaults(fn=cmd_import_legacy)


COMMANDS = {"version": _add_version, "init": _add_init, "check-strategy": _add_check_strategy, "doctor": _add_doctor,
            "import-legacy": _add_import_legacy}
