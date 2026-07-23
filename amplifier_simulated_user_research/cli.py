"""L4 CLI -- thin argparse front door over the L2 lib.

Console scripts: `amplifier-simulated-user-research` and the short alias
`asur` (see [project.scripts] in pyproject.toml). Subcommands:

    asur init [--dir DIR] [--force]      scaffold project.yaml + personas/
    asur run --config project.yaml       run one research round
    asur doctor [--config project.yaml]  environment diagnostics

This module contains NO pipeline logic -- see amplifier_simulated_user_research
for the lib, and pipelines/simulated-user-research.dot for the pipeline itself.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import cast

import yaml

from .config import RoundConfig, _package_repo_root
from .doctor import doctor
from .runner import run_round

_STARTER_PERSONAS = ("marisol", "dev", "ken")

_STARTER_CONFIG_COMMENT = """\
# project.yaml -- simulated-user-research round configuration.
# See the README for full field documentation. Fill in every value below
# before running `asur run --config project.yaml`.
"""


def _build_starter_config() -> RoundConfig:
    return RoundConfig(
        target_url="http://127.0.0.1:8000",
        seed_command="echo 'REPLACE ME: a shell command that seeds your scratch instance'",
        seed_cwd="/absolute/path/to/your/app",
        personas_dir="./personas",
        output_dir="./research-round-1",
        app_source_hint="/absolute/path/to/your/app/src",
        personas=list(_STARTER_PERSONAS),
        api_key_env="REPLACE_ME_API_KEY_ENV_VAR",
        browser_bundle="sur-browser-node",
        provider="anthropic",
    )


def cmd_init(args: argparse.Namespace) -> int:
    target_dir = Path(args.dir).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)

    config_path = target_dir / "project.yaml"
    if config_path.exists() and not args.force:
        print(
            f"asur init: {config_path} already exists (use --force to overwrite)",
            file=sys.stderr,
        )
        return 1

    starter = _build_starter_config()
    yaml_body = cast(str, yaml.safe_dump(starter.to_yaml_dict(), sort_keys=False))
    config_path.write_text(_STARTER_CONFIG_COMMENT + yaml_body, encoding="utf-8")
    print(f"asur init: wrote {config_path}")

    personas_dir = target_dir / "personas"
    personas_dir.mkdir(parents=True, exist_ok=True)
    shipped_personas_dir = _package_repo_root() / "personas"
    copied = []
    for name in _STARTER_PERSONAS:
        src = shipped_personas_dir / f"{name}.md"
        dst = personas_dir / f"{name}.md"
        if dst.exists() and not args.force:
            continue
        if src.is_file():
            shutil.copyfile(src, dst)
            copied.append(dst.name)
    if copied:
        print(f"asur init: wrote personas/{{{', '.join(copied)}}}")

    print(
        "asur init: next steps -- edit project.yaml (target_url, seed_command, "
        "seed_cwd, output_dir, app_source_hint, api_key/api_key_env), review/replace "
        "the starter personas, then run: asur run --config project.yaml"
    )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    try:
        config = RoundConfig.from_yaml(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"asur run: {e}", file=sys.stderr)
        return 1

    problems = config.validate()
    if problems:
        print("asur run: invalid config:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"asur run: config={args.config}")
    try:
        result = run_round(
            config, on_human_gate=args.on_human_gate, timeout_s=args.timeout_s
        )
    except (ValueError, RuntimeError) as e:
        print(f"asur run: {e}", file=sys.stderr)
        return 1

    print(f"asur run: status={result.status} exit_code={result.exit_code}")
    print(
        f"asur run: attractor_status={result.attractor_status!r} gate_reached={result.gate_reached}"
    )
    print(f"asur run: logs_dir={result.logs_dir}")
    print(f"asur run: artifacts={sorted(result.artifacts)}")
    if result.status == "failed":
        print("asur run: --- stderr tail ---", file=sys.stderr)
        print(result.stderr_tail, file=sys.stderr)

    return 0 if result.status in {"completed", "gate_reached"} else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    config: RoundConfig | None = None
    if args.config:
        try:
            config = RoundConfig.from_yaml(args.config)
        except (FileNotFoundError, ValueError) as e:
            print(f"asur doctor: {e}", file=sys.stderr)
            return 1

    checks = doctor(config)
    ok = True
    for check in checks:
        mark = "OK  " if check.ok else "FAIL"
        print(f"[{mark}] {check.name}: {check.detail}")
        ok = ok and check.ok

    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asur",
        description="Run simulated-user-research rounds (orchestrates the attractor .dot pipeline).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init", help="scaffold a project.yaml + personas/ starter")
    init_p.add_argument(
        "--dir", default=".", help="target directory (default: current directory)"
    )
    init_p.add_argument("--force", action="store_true", help="overwrite existing files")

    run_p = sub.add_parser("run", help="run one research round")
    run_p.add_argument("--config", required=True, help="path to project.yaml")
    run_p.add_argument(
        "--on-human-gate",
        choices=("fail", "auto-approve"),
        default="fail",
        help="how to handle the approval gate (default: fail -- correct for unattended runs)",
    )
    run_p.add_argument(
        "--timeout-s",
        type=float,
        default=None,
        help="optional subprocess-level backstop timeout in seconds",
    )

    doctor_p = sub.add_parser("doctor", help="environment diagnostics")
    doctor_p.add_argument(
        "--config",
        default=None,
        help="optional path to project.yaml for config-specific checks",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    dispatch = {
        "init": cmd_init,
        "run": cmd_run,
        "doctor": cmd_doctor,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
