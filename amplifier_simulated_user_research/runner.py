"""run_round() -- orchestrate one `attractor run` invocation of the pipeline.

This module builds and executes the exact command proven end-to-end
manually: `attractor run <dot> --cwd <seed_cwd> --param k=v ... --provider
<provider> --logs-root <dir> --on-human-gate <policy>`. It does NOT
reimplement any pipeline stage, retry policy, or prompt -- all of that
lives in pipelines/simulated-user-research.dot (see the DRY rule in the
amplifier-tool-leverage-patterns skill). This module's only job is: build
the invocation, run it, and inspect the artifacts the .dot's own
file-ground-truth contract already defines.
"""

from __future__ import annotations

import json
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
STATIC_ARTIFACTS = ("capture-notes.md", "review-ia.md", "review-responsive.md")


@dataclass
class RoundResult:
    """Outcome of one `run_round()` invocation.

    Attributes:
        status: "completed" (reached Exit without a pending gate),
            "gate_reached" (stopped at the human-approval gate --
            `research-spec.md` exists and `--on-human-gate fail` was in
            effect, which is SUCCESS for an unattended round), or "failed"
            (something else went wrong -- see stderr_tail/attractor_status).
        exit_code: The `attractor` subprocess's own exit code.
        attractor_status: The raw `status` field attractor's CLI prints as
            its final JSON line, if one could be parsed (None if the
            subprocess crashed before printing it).
        gate_reached: True iff the synthesis artifact exists at a
            meaningful size AND the run stopped short of exit_code 0 under
            an on_human_gate="fail" policy -- i.e. the pipeline did its
            job and is now waiting on a human.
        artifacts: Every contract artifact path that exists under
            output_dir, mapped to its Path (missing artifacts are omitted,
            not None -- check with `"research-spec.md" in result.artifacts`).
        logs_dir: The `--logs-root` directory attractor was given.
        command: The exact argv that was executed (for debugging/reporting).
        stdout_tail: Last 4000 chars of stdout.
        stderr_tail: Last 4000 chars of stderr.
    """

    status: str
    exit_code: int
    attractor_status: str | None
    gate_reached: bool
    artifacts: dict[str, Path] = field(default_factory=dict)
    logs_dir: Path | None = None
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
    config: RoundConfig, *, on_human_gate: str, logs_root: Path
) -> list[str]:
    cmd = resolve_attractor_command(config.attractor_checkout)
    cmd += ["run", str(_dot_path(config))]
    cmd += ["--cwd", str(Path(config.seed_cwd).expanduser())]
    cmd += ["--provider", config.provider]
    cmd += ["--logs-root", str(logs_root)]
    cmd += ["--on-human-gate", on_human_gate]
    for key, value in config.to_dot_params().items():
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
    spec_path = output_dir / SYNTHESIS_ARTIFACT
    if spec_path.is_file():
        found[SYNTHESIS_ARTIFACT] = spec_path
    return found


def run_round(
    config: RoundConfig,
    on_human_gate: str = "fail",
    *,
    timeout_s: float | None = None,
) -> RoundResult:
    """Run one simulated-user-research round via the `attractor` CLI.

    Builds and executes the same invocation shape proven manually:
    `attractor run <dot> --cwd <seed_cwd> --param k=v ... --provider
    <provider> --logs-root <dir> --on-human-gate <on_human_gate>`.

    Args:
        config: A validated RoundConfig (call `config.validate()` first if
            you want problems reported as a list rather than the first
            exception `to_dot_params()` raises).
        on_human_gate: "fail" (default; correct for unattended runs -- the
            pipeline stops at the approval gate once research-spec.md
            exists) or "auto-approve".
        timeout_s: Optional subprocess-level backstop. None (default)
            means no additional timeout beyond the pipeline's own
            per-node timeouts.

    Returns:
        RoundResult describing what happened and which artifacts exist.

    Raises:
        ValueError: if `config.validate()` reports problems.
        RuntimeError: if the `attractor` console script cannot be located.
    """
    problems = config.validate()
    if problems:
        raise ValueError("invalid RoundConfig: " + "; ".join(problems))

    output_dir = Path(config.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    logs_root = (
        Path(config.logs_root).expanduser()
        if config.logs_root
        else output_dir / ".attractor-logs"
    )
    logs_root.mkdir(parents=True, exist_ok=True)

    command = _build_command(config, on_human_gate=on_human_gate, logs_root=logs_root)

    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )

    attractor_status = _parse_attractor_status(proc.stdout)
    artifacts = _collect_artifacts(output_dir, config)

    spec_present = SYNTHESIS_ARTIFACT in artifacts
    gate_reached = bool(
        on_human_gate == "fail" and proc.returncode != 0 and spec_present
    )

    if proc.returncode == 0:
        status = "completed"
    elif gate_reached:
        status = "gate_reached"
    else:
        status = "failed"

    return RoundResult(
        status=status,
        exit_code=proc.returncode,
        attractor_status=attractor_status,
        gate_reached=gate_reached,
        artifacts=artifacts,
        logs_dir=logs_root,
        command=command,
        stdout_tail=proc.stdout[-4000:],
        stderr_tail=proc.stderr[-4000:],
    )
