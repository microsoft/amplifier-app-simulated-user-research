"""L4 CLI -- thin argparse front door over the L2 lib.

Console script: `amplifier-simulated-user-research` (the single command name;
see [project.scripts] in pyproject.toml). Subcommands:

    amplifier-simulated-user-research init [--dir DIR] [--force]
        scaffold project.yaml + personas/
    amplifier-simulated-user-research run --config project.yaml
        run one audit round
    amplifier-simulated-user-research triage --config project.yaml
        grade the latest run's findings
    amplifier-simulated-user-research doctor [--config project.yaml]
        environment diagnostics

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
from .provenance import (
    check_installed_build_staleness,
    describe_provenance,
    harness_provenance,
    provenance_differences,
)
from .runner import SYNTHESIS_ARTIFACT, run_round
from .triage import (
    ask_gate_verdict,
    find_round,
    latest_round,
    load_findings,
    precision_summary,
    record_triage,
    run_triage,
)

# The one command name (owner directive: full name, no acronym).
_PROG = "amplifier-simulated-user-research"

_STARTER_PERSONAS = ("marisol", "dev", "ken")
_PERSONA_TEMPLATE = "_TEMPLATE.md"

_STARTER_CONFIG_COMMENT = f"""\
# project.yaml -- simulated-user-research round configuration.
# See the README for full field documentation. Fill in every value below
# before running `{_PROG} run --config project.yaml`.
"""

# Shipped COMMENTED-OUT, not as a live `null`: absent is the legitimate default
# (most projects have no reset step, and the graph's stage-0 node safely no-ops),
# so a commented example teaches the key without adding an edit chore -- and
# without a placeholder that validation would have to reject.
_STARTER_RESET_COMMAND_HELP = """
# reset_command: OPTIONAL. A shell command that resets your app's own state,
#   run BEFORE seed_command -- once per round, from seed_cwd. A round must
#   start from a state representative of a real user's first encounter:
#   reusing a long-lived test fixture without resetting it silently corrupts
#   findings (two consecutive real rounds reported "triage is broken" when the
#   truth was a test queue a human had worked through days earlier). Omit it
#   if your seed_command already produces a clean state.
# reset_command: "python3 scripts/reset_research.py --yes"
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
        browser_bundle="simulated-user-research-browser-node",
        provider="anthropic",
    )


def cmd_init(args: argparse.Namespace) -> int:
    target_dir = Path(args.dir).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)

    config_path = target_dir / "project.yaml"
    if config_path.exists() and not args.force:
        print(
            f"{_PROG} init: {config_path} already exists (use --force to overwrite)",
            file=sys.stderr,
        )
        return 1

    starter = _build_starter_config()
    starter_dict = starter.to_yaml_dict()
    # Drop the live `reset_command: null` line -- it ships as a commented
    # example instead (see _STARTER_RESET_COMMAND_HELP for why).
    starter_dict.pop("reset_command", None)
    yaml_body = cast(str, yaml.safe_dump(starter_dict, sort_keys=False))
    config_path.write_text(
        _STARTER_CONFIG_COMMENT + yaml_body + _STARTER_RESET_COMMAND_HELP,
        encoding="utf-8",
    )
    print(f"{_PROG} init: wrote {config_path}")

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
        print(f"{_PROG} init: wrote personas/{{{', '.join(copied)}}}")

    print(_PERSONAS_BANNER)
    print(
        f"{_PROG} init: next steps --\n"
        "  1. Edit project.yaml: target_url, seed_command, seed_cwd, output_dir,\n"
        "     app_source_hint, and api_key or api_key_env (validation rejects the\n"
        "     REPLACE-ME placeholders until you do).\n"
        "  2. Rewrite each personas/*.md brief's session tasks for YOUR product\n"
        "     (keep identity + temperament; see personas/_TEMPLATE.md).\n"
        f"  3. Check your environment: {_PROG} doctor --config project.yaml\n"
        f"  4. Run the round: {_PROG} run --config project.yaml\n"
        "  5. When it stops at the gate: read research-spec.md, answer the gate,\n"
        f"     then grade the findings: {_PROG} triage --config project.yaml"
    )
    return 0


def _warn_if_installed_build_stale(config: RoundConfig) -> None:
    """Warn (stderr, non-blocking) when the installed build looks stale.

    A round costs an hour and real model spend; the operator should see
    this BEFORE that, not discover it later by grepping the installed
    wrapper (the incident this closes -- see AGENTS.md pitfall #10 and
    provenance.py's module docstring). Prints nothing when the comparison
    is "current" or "undetermined" (no nearby local checkout to compare
    against, the common case for a plain install) -- only an affirmatively
    detected difference is worth interrupting the operator for.
    """
    result = check_installed_build_staleness(config)
    if result.status != "stale":
        return
    print(
        f"{_PROG} run: warning -- the installed build looks stale: {result.detail}",
        file=sys.stderr,
    )
    print(
        f"{_PROG} run: merging a fix is not the same as shipping it -- "
        f"reinstall before this round tests anything real: "
        f"`uv tool install --force "
        f"git+https://github.com/microsoft/amplifier-app-simulated-user-research` "
        f"(or `uv sync` for a dev checkout). Proceeding anyway -- this is a "
        f"warning, not a block.",
        file=sys.stderr,
    )


def cmd_run(args: argparse.Namespace) -> int:
    try:
        config = RoundConfig.from_yaml(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"{_PROG} run: {e}", file=sys.stderr)
        return 1

    problems = config.validate()
    if problems:
        print(f"{_PROG} run: invalid config:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    _warn_if_installed_build_stale(config)

    if args.on_human_gate == "fail":
        print(
            f"{_PROG} run: note -- '--on-human-gate fail' is a deprecated alias for "
            "'stop' (same behavior: pause at the approval gate)",
            file=sys.stderr,
        )

    print(f"{_PROG} run: config={args.config}")
    try:
        result = run_round(
            config, on_human_gate=args.on_human_gate, timeout_s=args.timeout_s
        )
    except (ValueError, RuntimeError) as e:
        print(f"{_PROG} run: {e}", file=sys.stderr)
        return 1

    if result.gate_reached:
        spec_path = result.artifacts.get(SYNTHESIS_ARTIFACT, "research-spec.md")
        print(
            f"{_PROG} run: research spec ready -> {spec_path}; read it, then re-run "
            f"this command in a terminal to answer the gate (approve / request "
            f"revision)."
        )
        print(
            f"{_PROG} run: then grade the findings (~30s): "
            f"{_PROG} triage --config {args.config}"
        )
        print(f"{_PROG} run: run_id={result.run_id} ledger={result.rounds_path}")
        return 0

    print(f"{_PROG} run: run_id={result.run_id} status={result.status}")
    print(f"{_PROG} run: logs_dir={result.logs_dir}")
    print(f"{_PROG} run: artifacts={sorted(result.artifacts)}")
    if result.status == "failed":
        print(f"{_PROG} run: --- stderr tail ---", file=sys.stderr)
        print(result.stderr_tail, file=sys.stderr)

    return 0 if result.status == "completed" else 1


def _warn_on_harness_mismatch(
    config: RoundConfig,
    output_dir: Path,
    run_id: str,
    latest: dict,
) -> None:
    """Warn when findings came from a different harness build than the current one.

    A finding is only interpretable against the prompts that produced it. A
    round once reported a false "control does nothing" because it ran on a
    build predating the click-discipline prompt fix -- catching that took a
    manual grep of the installed wrapper. This is that grep, automated.

    Writes to stderr only; never blocks triage (the human may well be
    grading an old round on purpose).
    """
    record = latest if str(latest.get("run_id")) == run_id else None
    if record is None:
        record = find_round(output_dir, run_id) or {}

    recorded = record.get("harness")
    if not recorded:
        # Old ledger records predate this field. Say so plainly rather than
        # implying agreement we cannot verify.
        print(
            f"{_PROG} triage: note -- run {run_id} has no recorded harness "
            f"provenance (it predates that field), so the build that produced "
            f"these findings cannot be verified. Runs from now on record it.",
            file=sys.stderr,
        )
        return

    # include_engine=False: engine resolution identity-probes binaries with a
    # subprocess, and the engine path is not compared anyway (it legitimately
    # varies by machine) -- see provenance._COMPARED_FIELDS.
    current = harness_provenance(config, include_engine=False)
    differences = provenance_differences(recorded, current)
    if not differences:
        return

    print(
        f"{_PROG} triage: warning -- harness mismatch: run {run_id} was "
        f"produced by a different build than the one installed now "
        f"({'; '.join(differences)}). Findings reflect the harness that "
        f"produced them: a prompt-surface change may have fixed (or "
        f"introduced) the very behavior a finding describes. Re-run the "
        f"round before trusting a finding you cannot reproduce on the "
        f"current build.",
        file=sys.stderr,
    )
    print(
        f"{_PROG} triage: run harness: {describe_provenance(recorded)}",
        file=sys.stderr,
    )
    print(
        f"{_PROG} triage: current harness: {describe_provenance(current)}",
        file=sys.stderr,
    )


def cmd_triage(args: argparse.Namespace) -> int:
    try:
        config = RoundConfig.from_yaml(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"{_PROG} triage: {e}", file=sys.stderr)
        return 1

    output_dir = Path(config.output_dir).expanduser()

    record = latest_round(output_dir)
    if record is None:
        print(
            f"{_PROG} triage: no runs recorded in {output_dir}/rounds.jsonl -- "
            f"run a round first ({_PROG} run --config {args.config})",
            file=sys.stderr,
        )
        return 1
    run_id = args.run_id or str(record.get("run_id"))

    try:
        findings_doc = load_findings(output_dir)
    except (FileNotFoundError, ValueError) as e:
        print(f"{_PROG} triage: {e}", file=sys.stderr)
        return 1

    findings = findings_doc.get("findings", [])
    findings_run_id = findings_doc.get("run_id")
    if findings_run_id and findings_run_id != run_id:
        print(
            f"{_PROG} triage: warning -- findings.json is stamped "
            f"{findings_run_id!r} but you are triaging run {run_id!r}; the "
            f"findings may be stale (from an earlier run of this round).",
            file=sys.stderr,
        )

    _warn_on_harness_mismatch(config, output_dir, run_id, record)

    if not findings:
        print(
            f"{_PROG} triage: findings.json has an empty findings list -- "
            "nothing to grade"
        )
        return 0

    print(f"{_PROG} triage: grading {len(findings)} finding(s) for run {run_id}")
    gate_verdict = ask_gate_verdict(input)
    triage = run_triage(findings, input)

    rounds_path = output_dir / "rounds.jsonl"
    try:
        record_triage(rounds_path, run_id, gate_verdict, triage)
    except ValueError as e:
        print(f"{_PROG} triage: {e}", file=sys.stderr)
        return 1

    print(
        f"{_PROG} triage: recorded gate={gate_verdict} + {len(triage)} "
        f"verdicts -> {rounds_path}"
    )
    print(f"{_PROG} triage: {precision_summary(findings, triage)}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config: RoundConfig | None = None
    if args.config:
        try:
            config = RoundConfig.from_yaml(args.config)
        except (FileNotFoundError, ValueError) as e:
            print(f"{_PROG} doctor: {e}", file=sys.stderr)
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
        prog=_PROG,
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
        choices=("stop", "fail", "auto-approve", "console"),
        default="stop",
        help=(
            "stop: pause at the approval gate -- the normal ending for an "
            "unattended run (read research-spec.md, then re-run interactively "
            "to answer the gate). 'fail' is a deprecated alias for stop. "
            "'auto-approve' answers every gate with its first choice. "
            "console: answer the gate interactively in this terminal (approve "
            "/ request revision); requires an engine with console-gate support."
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
