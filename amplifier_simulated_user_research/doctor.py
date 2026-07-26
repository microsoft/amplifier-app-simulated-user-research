"""doctor() -- environment diagnostics for running a research round.

Cheap, safe, read-only checks. Never touches the target application or
runs a pipeline -- just answers "is my environment set up correctly?"
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import RoundConfig, _package_repo_root
from .provenance import check_installed_build_staleness
from .runner import resolve_attractor_resolution

PROVIDER_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

# Launch-probe timeouts (seconds). Kept short: doctor is a preflight, and a
# healthy agent-browser opens about:blank well inside these bounds.
_BROWSER_OPEN_TIMEOUT_S = 45
_BROWSER_CLOSE_TIMEOUT_S = 15


@dataclass
class DoctorCheck:
    """One diagnostic result: a named check, whether it passed, and detail.

    `warn` marks a check that passed (ok=True, exit code unaffected) but
    carries a condition the human should read before trusting a run's
    output -- e.g. persona briefs still byte-identical to the shipped
    roster (the run would produce findings about a product the briefs
    weren't written for).
    """

    name: str
    ok: bool
    detail: str
    warn: bool = False


def _check_attractor_cli(config: RoundConfig | None) -> DoctorCheck:
    """Resolve AND identity-validate the engine binary.

    Presence is not identity: `attractor` is a generic binary name and an
    unrelated tool of the same name shadowed ours on PATH once, turning a
    doctor [OK] into an inscrutable argparse failure mid-run. Resolution
    now probes each candidate's `run --help` for the flags we pass, so this
    check reports OK only for a binary that can actually run our pipeline.
    """
    attractor_checkout = config.attractor_checkout if config else None
    try:
        resolution = resolve_attractor_resolution(attractor_checkout)
    except RuntimeError as e:
        return DoctorCheck("attractor CLI", False, str(e))

    detail = f"resolved: {' '.join(resolution.command)} (via {resolution.source})"
    if resolution.source != "checkout":
        detail += "; identity-validated"

    notes: list[str] = []
    if resolution.rejected:
        notes.append(
            "candidates rejected: "
            + "; ".join(f"{path} -- {reason}" for path, reason in resolution.rejected)
        )

    # Cheap shadow check (no extra probe): if PATH's first `attractor` is not
    # the binary we resolved, a different tool of the same name is sitting in
    # front of ours. Not a failure -- sibling-first resolution already routed
    # around it -- but the operator should know it is there.
    if resolution.source != "checkout":
        path_first = shutil.which("attractor")
        if path_first and Path(path_first) != Path(resolution.command[0]):
            notes.append(
                f"note: PATH's first `attractor` is {path_first}, a different "
                f"binary; sibling-first resolution avoided it"
            )

    if notes:
        return DoctorCheck(
            "attractor CLI", True, f"{detail}. " + " ".join(notes), warn=True
        )
    return DoctorCheck("attractor CLI", True, detail)


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


def _detect_playwright_headless_shell() -> str | None:
    """Newest Playwright chromium_headless_shell binary on this box, if any.

    Playwright installs versioned builds under
    ~/.cache/ms-playwright/chromium_headless_shell-<build>/chrome-linux/headless_shell.
    Returns the highest-build binary that actually exists, else None. Sorted
    numerically (build 1181 > build 999 -- lexicographic sort gets this wrong).
    """
    base = Path.home() / ".cache" / "ms-playwright"
    if not base.is_dir():
        return None
    best: tuple[int, Path] | None = None
    for entry in base.glob("chromium_headless_shell-*"):
        m = re.search(r"(\d+)$", entry.name)
        if not m:
            continue
        binary = entry / "chrome-linux" / "headless_shell"
        if not binary.is_file():
            continue
        build = int(m.group(1))
        if best is None or build > best[0]:
            best = (build, binary)
    return str(best[1]) if best else None


def _browser_remediation() -> str:
    """Remediation text for a failed browser launch, covering both arches."""
    detected = _detect_playwright_headless_shell()
    if detected:
        arm64_path = f"{detected} (newest under ~/.cache/ms-playwright)"
    else:
        arm64_path = (
            "/path/to/chromium-or-headless_shell (none found under "
            "~/.cache/ms-playwright -- install one with "
            "`playwright install chromium-headless-shell`, or use a system chromium)"
        )
    return (
        "Remediation: on x86_64, run `agent-browser install`. On Linux ARM64 "
        "(Chrome-for-Testing ships NO linux-arm64 builds; `agent-browser "
        "install` exits 2 there), point agent-browser at your own binary: "
        f"export AGENT_BROWSER_EXECUTABLE_PATH={arm64_path} and "
        'AGENT_BROWSER_ARGS="--no-sandbox" -- or set the equivalent '
        "browser_executable_path / browser_args keys in project.yaml."
    )


def _probe_browser_launch(env: dict[str, str]) -> tuple[bool, str]:
    """Actually exercise `agent-browser open about:blank` + close.

    Presence on PATH isn't enough -- agent-browser can silently lose its
    managed browser (live incident: no Chrome-for-Testing builds exist for
    Linux ARM64, so every browser stage failed while the CLI itself looked
    healthy). Returns (ok, detail).

    Resilience: ALWAYS attempts `agent-browser close --all` afterwards so
    no daemon/state is left behind regardless of outcome. NOTE this also
    closes any other live agent-browser session on the box -- avoid running
    doctor while a research round is mid-flight.
    """
    exe = shutil.which("agent-browser")
    if not exe:
        return False, "agent-browser not found on PATH"
    try:
        proc = subprocess.run(
            [exe, "open", "about:blank"],
            capture_output=True,
            text=True,
            timeout=_BROWSER_OPEN_TIMEOUT_S,
            env=env,
            check=False,
        )
        if proc.returncode == 0:
            return True, "launched a real browser (open about:blank + close)"
        tail = (proc.stderr or proc.stdout or "").strip()[-300:]
        return (
            False,
            f"`agent-browser open about:blank` exited {proc.returncode}: {tail}",
        )
    except subprocess.TimeoutExpired:
        return (
            False,
            f"`agent-browser open about:blank` timed out after {_BROWSER_OPEN_TIMEOUT_S}s",
        )
    except OSError as e:
        return False, f"could not run agent-browser: {e}"
    finally:
        try:
            subprocess.run(
                [exe, "close", "--all"],
                capture_output=True,
                text=True,
                timeout=_BROWSER_CLOSE_TIMEOUT_S,
                env=env,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass


def _check_browser_launchable(config: RoundConfig | None) -> DoctorCheck:
    """FAIL (not warn) when agent-browser cannot actually launch a browser.

    Respects the current environment plus the config's browser_env()
    overrides (browser_executable_path / browser_args), so this tests
    exactly what run_round() would use.
    """
    env = dict(os.environ)
    overrides = config.browser_env() if config else {}
    env.update(overrides)

    configured_path = overrides.get("AGENT_BROWSER_EXECUTABLE_PATH")
    if configured_path and not Path(configured_path).is_file():
        return DoctorCheck(
            "browser launchable",
            False,
            f"configured browser_executable_path not found: {configured_path}. "
            + _browser_remediation(),
        )

    ok, detail = _probe_browser_launch(env)
    if ok:
        if overrides:
            detail += f" (using {', '.join(sorted(overrides))} from project.yaml)"
        return DoctorCheck("browser launchable", True, detail)
    return DoctorCheck(
        "browser launchable", False, f"{detail}. {_browser_remediation()}"
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
    """Locate bundles/browser-node-agent.yaml.

    The yaml MUST live in a package-free subdirectory (`bundles/`), never
    beside pyproject.toml: the Amplifier bundle activator editable-installs
    whatever package sits in the bundle yaml's own directory, which for a
    repo-root yaml meant installing this entire project into the CLI's venv
    (and failing on dependency-pin conflicts). See AGENTS.md pitfall 9.
    """
    bundle_path = sur_repo_dir / "bundles" / "browser-node-agent.yaml"
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
        f"amplifier bundle add file://<sur_repo_dir>/bundles/browser-node-agent.yaml "
        f"--name {browser_bundle}",
    )


def _check_personas_customized(
    personas_dir: str, personas: list[str], sur_repo_dir: Path
) -> DoctorCheck:
    """Warn when configured briefs are byte-identical to the shipped roster.

    The shipped briefs' session tasks name a SPECIFIC product's surfaces
    (a WhatsApp-triage product). Run unchanged against a different product,
    the personas probe screens that don't exist -- the findings will be
    fiction. This is a warning (ok=True, warn=True), not a failure: the one
    legitimate byte-identical case is auditing the product the roster was
    originally written for, and doctor cannot distinguish that from neglect.
    """
    p = Path(personas_dir).expanduser()
    shipped_dir = sur_repo_dir / "personas"
    identical: list[str] = []
    for name in personas:
        configured = p / f"{name}.md"
        shipped = shipped_dir / f"{name}.md"
        if not configured.is_file() or not shipped.is_file():
            continue
        try:
            if configured.read_bytes() == shipped.read_bytes():
                identical.append(f"{name}.md")
        except OSError:
            continue

    if identical:
        return DoctorCheck(
            "personas customized",
            True,
            f"{', '.join(identical)} byte-identical to the shipped roster -- "
            f"those briefs' session tasks were written for a WhatsApp-triage "
            f"product. Unchanged defaults against another product = findings "
            f"will be fiction. Rewrite session tasks for YOUR product (keep "
            f"identity + temperament; see personas/_TEMPLATE.md).",
            warn=True,
        )
    return DoctorCheck(
        "personas customized",
        True,
        "configured briefs differ from the shipped roster",
    )


def _check_installed_build_current(config: RoundConfig | None) -> DoctorCheck:
    """Warn when the installed build differs from a nearby local checkout.

    Merging a fix is not the same as shipping it: a round is only as good
    as the build that actually runs it, and two real incidents (see
    AGENTS.md pitfall #10 and provenance.py's module docstring) came from
    running a round against an installed build that predated a merged fix
    -- once discovered by a manual grep, once discovered only after the
    finding it produced was already "fixed" upstream. This check is that
    grep, run before a round instead of after one.

    Non-blocking by design (`warn=True`, `ok=True`) when stale: the
    operator may have a deliberate reason (e.g. auditing an older
    installed version on purpose). When no local checkout is discoverable
    -- the common case for a plain `uv tool install` -- this reports plain
    OK: "undetermined" is not evidence of a problem, and flagging it as a
    warning on every ordinary run would train people to ignore the signal.
    """
    result = check_installed_build_staleness(config)
    if result.status == "stale":
        return DoctorCheck("installed build current", True, result.detail, warn=True)
    return DoctorCheck("installed build current", True, result.detail)


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
        _check_browser_launchable(config),
        _check_provider_key(provider),
        _check_dot_file(sur_repo_dir),
        _check_scripts_dir(sur_repo_dir),
        _check_browser_bundle_yaml(sur_repo_dir),
        _check_installed_build_current(config),
    ]

    if config is not None:
        checks.append(_check_browser_bundle_registered(config.browser_bundle))
        checks.append(_check_personas_dir(config.personas_dir, config.personas))
        checks.append(
            _check_personas_customized(
                config.personas_dir, config.personas, sur_repo_dir
            )
        )

    return checks
