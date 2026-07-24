"""L4 CLI -- thin argparse front door over the L2 lib.

Console scripts: `amplifier-simulated-user-research` and the short alias
`asur` (see [project.scripts] in pyproject.toml). Subcommands:

    asur init [--dir DIR] [--force]      scaffold project.yaml + personas/
    asur run --config project.yaml       run one audit round
    asur triage --config project.yaml    grade the latest run's findings
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
from .runner import SYNTHESIS_ARTIFACT, run_round
from .triage import (
    ask_gate_verdict,
    latest_round,
    load_findings,
    precision_summary,
    record_triage,
    run_triage,
)

_STARTER_PERSONAS = ("marisol", "dev", "ken")
_PERSONA_TEMPLATE = "_TEMPLATE.md"

_STARTER_CONFIG_COMMENT = """\
# project.yaml -- simulated-user-research round configuration.
# See the README for full field documentation. Fill in every value below
# before running `asur run --config project.yaml`.
"""

_PERSONAS_BANNER = """\
============================================================================
  PERSONAS ARE PRODUCT-SPECIFIC. The copied briefs were written for a
  WhatsApp-triage product -- their session tasks name THAT product's
  surfaces. Running them unchanged against your product produces findings
  about screens that don't exist: fiction, not research.

  Rewrite each brief's session tasks for YOUR product (keep identity +
  temperament). Start from personas/_TEMPLATE.md -- it marks which parts
  are [PORTABLE] and which you must [REPLACE].
============================================================================\
"""


def _build_starter_config() -> RoundConfig:
    return RoundConfig(
        target_url="http://127.0.0.1:8000",
        seed_command="echo 'REPLACE ME: a shell command that seeds your scratch instance'",
        seed_cwd="/REPLACE_ME/absolute/path/to/your/app",
        personas_dir="./personas",
        output_dir="./research-round-1",
        app_source_hint="/REPLACE_ME/absolute/path/to/your/app/src",
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
    for name in (*_STARTER_PERSONAS, None):
        filename = _PERSONA_TEMPLATE if name is None else f"{name}.md"
        src = shipped_personas_dir / filename
        dst = personas_dir / filename
        if dst.exists() and not args.force:
            continue
        if src.is_file():
            shutil.copyfile(src, dst)
            copied.append(dst.name)
    if copied:
        print(f"asur init: wrote personas/{{{', '.join(copied)}}}")

    print(_PERSONAS_BANNER)
    print(
        "asur init: next steps --\n"
        "  1. Edit project.yaml: target_url, seed_command, seed_cwd, output_dir,\n"
        "     app_source_hint, and api_key or api_key_env (validation rejects the\n"
        "     REPLACE-ME placeholders until you do).\n"
        "  2. Rewrite each personas/*.md brief's session tasks for YOUR product\n"
        "     (keep identity + temperament; see personas/_TEMPLATE.md).\n"
        "  3. Check your environment: asur doctor --config project.yaml\n"
        "  4. Run the round: asur run --config project.yaml\n"
        "  5. When it stops at the gate: read research-spec.md, answer the gate,\n"
        "     then grade the findings: asur triage --config project.yaml"
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

    if args.on_human_gate == "fail":
        print(
            "asur run: note -- '--on-human-gate fail' is a deprecated alias for "
            "'stop' (same behavior: pause at the approval gate)",
            file=sys.stderr,
        )

    print(f"asur run: config={args.config}")
    try:
        result = run_round(
            config, on_human_gate=args.on_human_gate, timeout_s=args.timeout_s
        )
    except (ValueError, RuntimeError) as e:
        print(f"asur run: {e}", file=sys.stderr)
        return 1

    if result.gate_reached:
        spec_path = result.artifacts.get(SYNTHESIS_ARTIFACT, "research-spec.md")
        print(
            f"asur run: research spec ready -> {spec_path}; read it, then re-run "
            f"this command in a terminal to answer the gate (approve / request "
            f"revision)."
        )
        print(
            f"asur run: then grade the findings (~30s): "
            f"asur triage --config {args.config}"
        )
        print(f"asur run: run_id={result.run_id} ledger={result.rounds_path}")
        return 0

    print(f"asur run: run_id={result.run_id} status={result.status}")
    print(f"asur run: logs_dir={result.logs_dir}")
    print(f"asur run: artifacts={sorted(result.artifacts)}")
    if result.status == "failed":
        print("asur run: --- stderr tail ---", file=sys.stderr)
        print(result.stderr_tail, file=sys.stderr)

    return 0 if result.status == "completed" else 1


def cmd_triage(args: argparse.Namespace) -> int:
    try:
        config = RoundConfig.from_yaml(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"asur triage: {e}", file=sys.stderr)
        return 1

    output_dir = Path(config.output_dir).expanduser()

    record = latest_round(output_dir)
    if record is None:
        print(
            f"asur triage: no runs recorded in {output_dir}/rounds.jsonl -- "
            f"run a round first (asur run --config {args.config})",
            file=sys.stderr,
        )
        return 1
    run_id = args.run_id or str(record.get("run_id"))

    try:
        findings_doc = load_findings(output_dir)
    except (FileNotFoundError, ValueError) as e:
        print(f"asur triage: {e}", file=sys.stderr)
        return 1

    findings = findings_doc.get("findings", [])
    findings_run_id = findings_doc.get("run_id")
    if findings_run_id and findings_run_id != run_id:
        print(
            f"asur triage: warning -- findings.json is stamped {findings_run_id!r} "
            f"but you are triaging run {run_id!r}; the findings may be stale "
            f"(from an earlier run of this round).",
            file=sys.stderr,
        )

    if not findings:
        print(
            "asur triage: findings.json has an empty findings list -- nothing to grade"
        )
        return 0

    print(f"asur triage: grading {len(findings)} finding(s) for run {run_id}")
    gate_verdict = ask_gate_verdict(input)
    triage = run_triage(findings, input)

    rounds_path = output_dir / "rounds.jsonl"
    try:
        record_triage(rounds_path, run_id, gate_verdict, triage)
    except ValueError as e:
        print(f"asur triage: {e}", file=sys.stderr)
        return 1

    print(
        f"asur triage: recorded gate={gate_verdict} + {len(triage)} verdicts -> {rounds_path}"
    )
    print(f"asur triage: {precision_summary(findings, triage)}")
    return 0


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
        if check.ok and check.warn:
            mark = "WARN"
        elif check.ok:
            mark = "OK  "
        else:
            mark = "FAIL"
        print(f"[{mark}] {check.name}: {check.detail}")
        ok = ok and check.ok

    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asur",
        description=(
            "Run simulated-user-research audit rounds "
            "(orchestrates the attractor .dot pipeline)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init", help="scaffold a project.yaml + personas/ starter")
    init_p.add_argument(
        "--dir", default=".", help="target directory (default: current directory)"
    )
    init_p.add_argument("--force", action="store_true", help="overwrite existing files")

    run_p = sub.add_parser("run", help="run one audit round")
    run_p.add_argument("--config", required=True, help="path to project.yaml")
    run_p.add_argument(
        "--on-human-gate",
        choices=("stop", "fail", "auto-approve"),
        default="stop",
        help=(
            "stop: pause at the approval gate -- the normal ending for an "
            "unattended run (read research-spec.md, then re-run interactively "
            "to answer the gate). 'fail' is a deprecated alias for stop. "
            "'auto-approve' answers every gate with its first choice."
        ),
    )
    run_p.add_argument(
        "--timeout-s",
        type=float,
        default=None,
        help="optional subprocess-level backstop timeout in seconds",
    )

    triage_p = sub.add_parser(
        "triage",
        help="grade the latest run's findings (real / noise / wont-fix) + gate verdict",
    )
    triage_p.add_argument("--config", required=True, help="path to project.yaml")
    triage_p.add_argument(
        "--run-id",
        default=None,
        help="triage a specific run instead of the latest recorded one",
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
        "triage": cmd_triage,
        "doctor": cmd_doctor,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
