"""doctor() -- environment diagnostics for running a research round.

Cheap, safe, read-only checks. Never touches the target application or
runs a pipeline -- just answers "is my environment set up correctly?"
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import RoundConfig, _package_repo_root
from .runner import resolve_attractor_command

PROVIDER_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


@dataclass
class DoctorCheck:
    """One diagnostic result: a named check, whether it passed, and detail."""

    name: str
    ok: bool
    detail: str


def _check_attractor_cli(config: RoundConfig | None) -> DoctorCheck:
    attractor_checkout = config.attractor_checkout if config else None
    try:
        command = resolve_attractor_command(attractor_checkout)
    except RuntimeError as e:
        return DoctorCheck("attractor CLI", False, str(e))
    return DoctorCheck("attractor CLI", True, f"resolved: {' '.join(command)}")


def _check_pipeline_runner_importable() -> DoctorCheck:
    try:
        import amplifier_module_pipeline_runner  # noqa: F401
    except ImportError as e:
        return DoctorCheck(
            "amplifier_module_pipeline_runner importable",
            False,
            f"not importable: {e}",
        )
    return DoctorCheck("amplifier_module_pipeline_runner importable", True, "OK")


def _check_agent_browser() -> DoctorCheck:
    exe = shutil.which("agent-browser")
    if exe:
        return DoctorCheck("agent-browser on PATH", True, exe)
    return DoctorCheck(
        "agent-browser on PATH",
        False,
        "not found -- install github.com/vercel-labs/agent-browser; "
        "required by the capture/persona browser tool-nodes",
    )


def _check_provider_key(provider: str) -> DoctorCheck:
    env_name = PROVIDER_KEY_ENV.get(provider, "ANTHROPIC_API_KEY")
    present = bool(os.environ.get(env_name))
    if present:
        return DoctorCheck(
            f"{provider} provider key ({env_name})", True, "present in environment"
        )
    return DoctorCheck(
        f"{provider} provider key ({env_name})",
        False,
        f"{env_name} is not set -- required for the pipeline's LLM (box) nodes",
    )


def _check_dot_file(sur_repo_dir: Path) -> DoctorCheck:
    dot_path = sur_repo_dir / "pipelines" / "simulated-user-research.dot"
    if dot_path.is_file():
        return DoctorCheck("pipeline .dot present", True, str(dot_path))
    return DoctorCheck("pipeline .dot present", False, f"not found: {dot_path}")


def _check_scripts_dir(sur_repo_dir: Path) -> DoctorCheck:
    script_path = sur_repo_dir / "scripts" / "run_browser_node.py"
    if script_path.is_file():
        return DoctorCheck(
            "scripts/run_browser_node.py present", True, str(script_path)
        )
    return DoctorCheck(
        "scripts/run_browser_node.py present", False, f"not found: {script_path}"
    )


def _check_browser_bundle_yaml(sur_repo_dir: Path) -> DoctorCheck:
    bundle_path = sur_repo_dir / "browser-node-agent.yaml"
    if bundle_path.is_file():
        return DoctorCheck("browser-node-agent.yaml present", True, str(bundle_path))
    return DoctorCheck(
        "browser-node-agent.yaml present", False, f"not found: {bundle_path}"
    )


def _check_browser_bundle_registered(browser_bundle: str) -> DoctorCheck:
    amplifier_exe = shutil.which("amplifier")
    if not amplifier_exe:
        return DoctorCheck(
            f"bundle {browser_bundle!r} registered",
            False,
            "amplifier CLI not found on PATH -- cannot check bundle registry",
        )
    try:
        proc = subprocess.run(
            [amplifier_exe, "bundle", "list", "--all", "--format", "text"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return DoctorCheck(
            f"bundle {browser_bundle!r} registered",
            False,
            f"could not run `amplifier bundle list`: {e}",
        )

    if browser_bundle in proc.stdout:
        return DoctorCheck(
            f"bundle {browser_bundle!r} registered",
            True,
            "found in `amplifier bundle list --all`",
        )
    return DoctorCheck(
        f"bundle {browser_bundle!r} registered",
        False,
        f"not found in `amplifier bundle list --all`. Register it with: "
        f"amplifier bundle add file://<sur_repo_dir>/browser-node-agent.yaml --name {browser_bundle}",
    )


def _check_personas_dir(personas_dir: str, personas: list[str]) -> DoctorCheck:
    p = Path(personas_dir).expanduser()
    if not p.is_dir():
        return DoctorCheck("personas_dir", False, f"not a directory: {p}")
    missing = [name for name in personas if not (p / f"{name}.md").is_file()]
    if missing:
        return DoctorCheck(
            "personas_dir",
            False,
            f"{p} exists but missing brief(s): {', '.join(f'{m}.md' for m in missing)}",
        )
    return DoctorCheck(
        "personas_dir", True, f"{p} has all {len(personas)} persona brief(s)"
    )


def doctor(config: RoundConfig | None = None) -> list[DoctorCheck]:
    """Run environment diagnostics; returns one DoctorCheck per condition.

    Args:
        config: Optional RoundConfig. When given, adds config-specific
            checks (personas_dir contents, the configured provider's key,
            the configured browser_bundle's registration). When omitted,
            only the environment-wide checks run (attractor CLI, pipeline
            module import, agent-browser, this repo's own files).

    Returns:
        A list of DoctorCheck; iterate and check `.ok` -- this function
        never raises for a failed check, only for programmer error.
    """
    sur_repo_dir = config.resolved_sur_repo_dir() if config else _package_repo_root()
    provider = config.provider if config else "anthropic"

    checks = [
        _check_attractor_cli(config),
        _check_pipeline_runner_importable(),
        _check_agent_browser(),
        _check_provider_key(provider),
        _check_dot_file(sur_repo_dir),
        _check_scripts_dir(sur_repo_dir),
        _check_browser_bundle_yaml(sur_repo_dir),
    ]

    if config is not None:
        checks.append(_check_browser_bundle_registered(config.browser_bundle))
        checks.append(_check_personas_dir(config.personas_dir, config.personas))

    return checks
