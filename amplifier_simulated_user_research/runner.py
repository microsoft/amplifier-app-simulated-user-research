"""run_round() -- orchestrate one `attractor run` invocation of the pipeline.

This module builds and executes the exact command proven end-to-end
manually: `attractor run <dot> --cwd <seed_cwd> --param k=v ... --provider
<provider> --logs-root <dir> --on-human-gate <policy>`. It does NOT
reimplement any pipeline stage, retry policy, or prompt -- all of that
lives in pipelines/simulated-user-research.dot (see the DRY rule in the
amplifier-tool-leverage-patterns skill). This module's only job is: build
the invocation, run it, inspect the artifacts the .dot's own
file-ground-truth contract already defines, and keep the run ledger
(`rounds.jsonl`) honest.

Run identity: every run gets a `run_id` (``r-YYYYMMDD-HHMMSS``, local
time -- the ledger is a human-facing record on the machine that ran it).
The id is passed into the graph as ``--param run_id=<id>`` (the graph
stamps it into its artifacts) and recorded in ``rounds.jsonl``.

The ledger: ``<output_dir>/rounds.jsonl`` -- one JSON record appended per
run. It lives IN output_dir (not its parent) so the ledger travels with
the artifacts it describes and two projects sharing a parent directory
can never interleave their histories. Each record links to the previous
run of the same project via ``prior_run_id`` (the previous line's run_id).
``gate`` and ``triage`` start null and are filled in later by the triage
flow (see triage.py / `asur triage`).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .config import RoundConfig

# Artifact filenames the .dot graph's own contract defines (see
# pipelines/simulated-user-research.dot's per-stage `check_*`/`verify_*`
# nodes). Kept here as the one place this orchestration layer looks for
# them -- NOT a duplicate of pipeline logic, just the file-contract surface
# a caller needs to inspect after a run.
SYNTHESIS_ARTIFACT = "research-spec.md"
FINDINGS_ARTIFACT = "findings.json"
STATIC_ARTIFACTS = ("capture-notes.md", "review-ia.md", "review-responsive.md")

# The run ledger filename, kept in output_dir (see module docstring for why
# output_dir and not its parent).
ROUNDS_LEDGER = "rounds.jsonl"

# run_id format: r-YYYYMMDD-HHMMSS (local time).
RUN_ID_PATTERN = re.compile(r"^r-\d{8}-\d{6}$")

# Human-facing gate policy -> the engine's `--on-human-gate` value.
# "stop" is the documented choice; "fail" is kept as a deprecated alias
# (the engine itself only knows fail/auto-approve -- "stop" describes what
# actually happens: the pipeline pauses at the approval gate, which is the
# normal ending for an unattended run).
_GATE_POLICY_TO_ENGINE = {
    "stop": "fail",
    "fail": "fail",  # deprecated alias for "stop"
    "auto-approve": "auto-approve",
}


def generate_run_id(now: _dt.datetime | None = None) -> str:
    """Return a fresh run id: ``r-YYYYMMDD-HHMMSS`` (local time)."""
    now = now or _dt.datetime.now()
    return now.strftime("r-%Y%m%d-%H%M%S")


def normalize_gate_policy(on_human_gate: str) -> str:
    """Map the human-facing gate policy to the engine's flag value.

    Raises:
        ValueError: on an unknown policy name.
    """
    try:
        return _GATE_POLICY_TO_ENGINE[on_human_gate]
    except KeyError:
        raise ValueError(
            f"unknown on_human_gate policy {on_human_gate!r} "
            f"(known: {', '.join(sorted(_GATE_POLICY_TO_ENGINE))})"
        ) from None


@dataclass
class RoundResult:
    """Outcome of one `run_round()` invocation.

    Attributes:
        run_id: This run's identity (``r-YYYYMMDD-HHMMSS``); also recorded
            in the ``rounds.jsonl`` ledger and passed into the graph as
            ``--param run_id``.
        status: "completed" (reached Exit without a pending gate),
            "gate_reached" (stopped at the human-approval gate --
            `research-spec.md` exists and the gate policy was "stop"
            (or its deprecated alias "fail"), which is SUCCESS for an
            unattended round), or "failed" (something else went wrong --
            see stderr_tail/attractor_status).
        exit_code: The `attractor` subprocess's own exit code.
        attractor_status: The raw `status` field attractor's CLI prints as
            its final JSON line, if one could be parsed (None if the
            subprocess crashed before printing it).
        gate_reached: True iff the synthesis artifact exists at a
            meaningful size AND the run stopped short of exit_code 0 under
            a "stop"/"fail" gate policy -- i.e. the pipeline did its job
            and is now waiting on a human.
        artifacts: Every contract artifact path that exists under
            output_dir, mapped to its Path (missing artifacts are omitted,
            not None -- check with `"research-spec.md" in result.artifacts`).
        logs_dir: This run's own logs directory (a per-run subdirectory
            named after run_id, so per-stage timings and forensics never
            mix across runs).
        rounds_path: Path to the ``rounds.jsonl`` ledger this run was
            appended to.
        command: The exact argv that was executed (for debugging/reporting).
        stdout_tail: Last 4000 chars of stdout.
        stderr_tail: Last 4000 chars of stderr.
    """

    run_id: str
    status: str
    exit_code: int
    attractor_status: str | None
    gate_reached: bool
    artifacts: dict[str, Path] = field(default_factory=dict)
    logs_dir: Path | None = None
    rounds_path: Path | None = None
    command: list[str] = field(default_factory=list)
    stdout_tail: str = ""
    stderr_tail: str = ""


def _dot_path(config: RoundConfig) -> Path:
    return config.resolved_sur_repo_dir() / "pipelines" / "simulated-user-research.dot"


def resolve_attractor_command(attractor_checkout: str | None = None) -> list[str]:
    """Return the argv prefix that runs the `attractor` CLI.

    Primary path (default, `attractor_checkout=None`):
    `amplifier-module-pipeline-runner` is a normal git-subdirectory
    dependency of this package (verified installable -- see README "Engine
    dependency"), so its `attractor` console script is installed into the
    same environment as this package. We locate it via PATH first, then
    fall back to the sibling of the current Python interpreter (the
    standard location for console scripts installed into the active venv)
    -- this is more robust than PATH alone in non-interactive subprocess
    contexts.

    Escape hatch: if `attractor_checkout` is given (local development
    against an unmerged/local amplifier-bundle-attractor checkout), shell
    out via `uv run --project <checkout>/modules/pipeline-runner attractor`
    instead -- this keeps the dependency behind attractor's own CLI
    boundary either way (never importing its internals).

    Raises:
        RuntimeError: if no `attractor` command can be located.
    """
    if attractor_checkout:
        runner_dir = (
            Path(attractor_checkout).expanduser() / "modules" / "pipeline-runner"
        )
        if not runner_dir.is_dir():
            raise RuntimeError(
                f"attractor_checkout={attractor_checkout!r} does not contain "
                f"modules/pipeline-runner (looked for {runner_dir})"
            )
        return ["uv", "run", "--project", str(runner_dir), "attractor"]

    exe = shutil.which("attractor")
    if exe:
        return [exe]

    candidate = Path(sys.executable).parent / "attractor"
    if candidate.is_file():
        return [str(candidate)]

    raise RuntimeError(
        "attractor console script not found on PATH or next to the current Python "
        "interpreter. amplifier-module-pipeline-runner should have installed it as "
        "part of this package's own dependencies -- reinstall (`uv sync` / `pip "
        "install -e .`), or set RoundConfig.attractor_checkout to point at a local "
        "amplifier-bundle-attractor checkout."
    )


def _build_command(
    config: RoundConfig, *, engine_gate_policy: str, logs_root: Path, run_id: str
) -> list[str]:
    cmd = resolve_attractor_command(config.attractor_checkout)
    cmd += ["run", str(_dot_path(config))]
    cmd += ["--cwd", str(Path(config.seed_cwd).expanduser())]
    cmd += ["--provider", config.provider]
    cmd += ["--logs-root", str(logs_root)]
    cmd += ["--on-human-gate", engine_gate_policy]
    params = config.to_dot_params()
    params["run_id"] = run_id
    for key, value in params.items():
        cmd += ["--param", f"{key}={value}"]
    return cmd


def _parse_attractor_status(stdout: str) -> str | None:
    """Find the `attractor: status=...` line the CLI always prints.

    More robust than parsing the trailing JSON blob (which is on its own
    line but easy to mis-locate if stdout is interleaved) -- this exact
    prefix is printed unconditionally by `cmd_run` before anything else.
    """
    for line in stdout.splitlines():
        if line.startswith("attractor: status="):
            return line[len("attractor: status=") :].strip()
    # Fall back to the JSON summary line (find the last '{' start, like
    # run_browser_node.py does for the analogous single-shot-session case).
    brace_idx = stdout.rfind("{")
    if brace_idx == -1:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(stdout[brace_idx:])
    except json.JSONDecodeError:
        return None
    status = data.get("status")
    return str(status) if status is not None else None


def _collect_artifacts(output_dir: Path, config: RoundConfig) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for name in STATIC_ARTIFACTS:
        path = output_dir / name
        if path.is_file():
            found[name] = path
    for persona in config.personas:
        name = f"persona-{persona}.md"
        path = output_dir / name
        if path.is_file():
            found[name] = path
    for name in (SYNTHESIS_ARTIFACT, FINDINGS_ARTIFACT):
        path = output_dir / name
        if path.is_file():
            found[name] = path
    return found


def _mine_per_stage_wall_clock(logs_dir: Path) -> dict[str, float] | None:
    """Mine per-stage wall-clock seconds from the engine's per-node logs.

    The attractor engine writes ``<logs_dir>/<node_id>/status.json`` with a
    ``duration_ms`` field for every node it executed. That is the honest,
    machine-recorded source -- we read it rather than re-deriving timings
    from file mtimes. Nodes without a readable ``duration_ms`` are simply
    omitted (honest N/A, not a guess); if NOTHING is derivable (no logs
    dir, no status.json files at all), returns None so the ledger records
    an explicit null instead of a misleading empty mapping.
    """
    if not logs_dir.is_dir():
        return None

    stages: dict[str, float] = {}
    for node_dir in sorted(logs_dir.iterdir()):
        status_path = node_dir / "status.json"
        if not node_dir.is_dir() or not status_path.is_file():
            continue
        try:
            data = json.loads(status_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        duration_ms = data.get("duration_ms")
        if isinstance(duration_ms, (int, float)):
            stages[node_dir.name] = round(duration_ms / 1000.0, 3)

    return stages or None


def _read_prior_run_id(rounds_path: Path) -> str | None:
    """Return the run_id of the last record in the ledger, if any.

    Malformed trailing lines are tolerated (skipped backwards) -- a
    corrupt ledger line must not block recording the next run.
    """
    if not rounds_path.is_file():
        return None
    try:
        lines = rounds_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        run_id = record.get("run_id")
        if run_id:
            return str(run_id)
    return None


def _append_round_record(rounds_path: Path, record: dict) -> None:
    with open(rounds_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def run_round(
    config: RoundConfig,
    on_human_gate: str = "stop",
    *,
    timeout_s: float | None = None,
    run_id: str | None = None,
) -> RoundResult:
    """Run one simulated-user-research round via the `attractor` CLI.

    Builds and executes the same invocation shape proven manually:
    `attractor run <dot> --cwd <seed_cwd> --param k=v ... --provider
    <provider> --logs-root <dir> --on-human-gate <policy>`, then appends
    one record to the ``rounds.jsonl`` ledger in output_dir.

    Args:
        config: A validated RoundConfig (call `config.validate()` first if
            you want problems reported as a list rather than the first
            exception `to_dot_params()` raises).
        on_human_gate: "stop" (default; pause at the approval gate -- the
            normal ending for an unattended run: read research-spec.md,
            then re-run interactively to answer the gate), "fail"
            (deprecated alias for "stop"), or "auto-approve".
        timeout_s: Optional subprocess-level backstop. None (default)
            means no additional timeout beyond the pipeline's own
            per-node timeouts.
        run_id: Optional explicit run id (must match ``r-YYYYMMDD-HHMMSS``);
            generated from the current time when omitted. Exposed mainly
            for tests and replay tooling.

    Returns:
        RoundResult describing what happened and which artifacts exist.

    Raises:
        ValueError: if `config.validate()` reports problems, the gate
            policy is unknown, or an explicit run_id is malformed.
        RuntimeError: if the `attractor` console script cannot be located.
    """
    problems = config.validate()
    if problems:
        raise ValueError("invalid RoundConfig: " + "; ".join(problems))

    engine_gate_policy = normalize_gate_policy(on_human_gate)

    if run_id is None:
        run_id = generate_run_id()
    elif not RUN_ID_PATTERN.match(run_id):
        raise ValueError(
            f"run_id {run_id!r} does not match the required format r-YYYYMMDD-HHMMSS"
        )

    output_dir = Path(config.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Per-run logs isolation: each run gets its own subdirectory named
    # after run_id, so per-stage timings/status.json forensics never mix
    # across runs of the same project.
    logs_base = (
        Path(config.logs_root).expanduser()
        if config.logs_root
        else output_dir / ".attractor-logs"
    )
    logs_dir = logs_base / run_id
    logs_dir.mkdir(parents=True, exist_ok=True)

    command = _build_command(
        config, engine_gate_policy=engine_gate_policy, logs_root=logs_dir, run_id=run_id
    )

    # Custom-browser passthrough (see RoundConfig.browser_env): merged over
    # the caller's environment so project.yaml can carry the ARM64/custom
    # browser fix (AGENT_BROWSER_EXECUTABLE_PATH / AGENT_BROWSER_ARGS)
    # instead of the shell.
    env = {**os.environ, **config.browser_env()}

    ts_start = _dt.datetime.now(_dt.timezone.utc)
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
        env=env,
    )
    ts_end = _dt.datetime.now(_dt.timezone.utc)

    attractor_status = _parse_attractor_status(proc.stdout)
    artifacts = _collect_artifacts(output_dir, config)

    spec_present = SYNTHESIS_ARTIFACT in artifacts
    gate_reached = bool(
        engine_gate_policy == "fail" and proc.returncode != 0 and spec_present
    )

    if proc.returncode == 0:
        status = "completed"
    elif gate_reached:
        status = "gate_reached"
    else:
        status = "failed"

    rounds_path = output_dir / ROUNDS_LEDGER
    record = {
        "run_id": run_id,
        "ts_start": ts_start.isoformat(),
        "ts_end": ts_end.isoformat(),
        "status": status,
        "gate_reached": gate_reached,
        "artifacts": sorted(artifacts),
        "wall_clock_s": round((ts_end - ts_start).total_seconds(), 3),
        "per_stage_wall_clock": _mine_per_stage_wall_clock(logs_dir),
        "prior_run_id": _read_prior_run_id(rounds_path),
        "gate": None,
        "triage": None,
    }
    _append_round_record(rounds_path, record)

    return RoundResult(
        run_id=run_id,
        status=status,
        exit_code=proc.returncode,
        attractor_status=attractor_status,
        gate_reached=gate_reached,
        artifacts=artifacts,
        logs_dir=logs_dir,
        rounds_path=rounds_path,
        command=command,
        stdout_tail=proc.stdout[-4000:],
        stderr_tail=proc.stderr[-4000:],
    )
