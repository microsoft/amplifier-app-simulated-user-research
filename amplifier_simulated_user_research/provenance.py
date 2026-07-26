"""Harness provenance -- which build of this tool produced a round's findings.

WHY THIS EXISTS (a real incident, and the rule it bought):

A round produced three CRITICAL/HIGH findings of the shape "control X does
nothing." Two were real. One was a false positive from a known harness
artifact -- clicking an element outside the viewport reports success but
never lands -- which had ALREADY been fixed by a click-discipline block
added to the browser-driving prompts. The round had simply been run with an
installed build that predated the fix, and nothing in the run's own records
said so. It was caught by manually grepping the installed wrapper for the
string "CLICK DISCIPLINE". That is luck, not process.

So: a round's findings are only interpretable against the harness build
that produced them, and the ledger records that build for every run.

WHAT IS RECORDED (a handful of scalars -- provenance, not telemetry):

    tool_version     installed distribution version of this package
    wrapper_sha256   sha256[:12] of scripts/run_browser_node.py
    pipeline_sha256  sha256[:12] of pipelines/simulated-user-research.dot
    engine_source    how the attractor binary was resolved
    engine_path      which attractor binary was used

The two file hashes are the load-bearing fields. `tool_version` alone is
necessary but NOT sufficient: this repo does not bump the version per PR
(the incident's before/after builds were both `0.1.0`), so only content
hashes distinguish them. Those two files are hashed because they are the
prompt-shaping surface -- PRINCIPLES.md #1: the `.dot` is the sole logic
home for stage prompts, with browser-session prompts living in the wrapper
as the one documented exception. Between them they carry every instruction
that shapes what an agent does and therefore what a finding says.

TRUTHFULNESS RULE: every field is individually best-effort and OMITTED when
it cannot be derived honestly -- an absent key beats a fabricated one, and
provenance must never fail a run it is only describing.
"""

from __future__ import annotations

import hashlib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .config import RoundConfig

# Distribution name (pyproject `[project] name`), for importlib.metadata.
_DIST_NAME = "amplifier-app-simulated-user-research"

# Files whose CONTENT shapes agent behavior, mapped to their ledger key.
# Paths are relative to the resolved repo root (source checkout or the
# wheel's `_bundled/` tree -- see config._package_repo_root).
_HASHED_SURFACES = {
    "wrapper_sha256": Path("scripts") / "run_browser_node.py",
    "pipeline_sha256": Path("pipelines") / "simulated-user-research.dot",
}

# Hash prefix length: enough to distinguish builds by eye in a ledger line,
# short enough to stay readable. Collisions are not a threat model here --
# this detects "different build", not tampering.
_HASH_CHARS = 12

# Fields compared when warning about a harness mismatch at triage time.
# Deliberately EXCLUDES engine_path/engine_source: those legitimately differ
# by machine, venv, and install method, so comparing them would produce
# noisy warnings that train people to ignore the signal. Only the surfaces
# that shape agent behavior are compared.
_COMPARED_FIELDS = ("tool_version", "wrapper_sha256", "pipeline_sha256")


def _short_sha256(path: Path) -> str | None:
    """sha256[:12] of a file's bytes, or None if it can't be read."""
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None
    return digest[:_HASH_CHARS]


def _tool_version() -> str | None:
    """Installed distribution version, or None if it can't be determined."""
    try:
        return version(_DIST_NAME)
    except PackageNotFoundError:
        # Running from a source tree that was never installed -- the honest
        # answer is "unknown", so omit the key rather than guess.
        return None


def harness_provenance(
    config: RoundConfig, *, include_engine: bool = True
) -> dict[str, str]:
    """Best-effort provenance for the harness that is about to run (or ran).

    Args:
        config: the round's config -- supplies the repo root whose files are
            hashed (`sur_repo_dir`, or the installed package's bundled tree).
        include_engine: when True, also record which `attractor` binary was
            resolved and how. Callers on a hot interactive path should pass
            False: engine resolution identity-probes candidate binaries with
            a subprocess (see runner.resolve_attractor_resolution), which is
            free during a run (already resolved and cached) but not worth a
            subprocess just to print a triage warning.

    Returns:
        A dict of scalar provenance fields. Any field that cannot be derived
        truthfully is OMITTED, so the result may be partial -- or, in a badly
        broken environment, empty. Never raises.
    """
    provenance: dict[str, str] = {}

    tool_version = _tool_version()
    if tool_version:
        provenance["tool_version"] = tool_version

    try:
        repo_root = config.resolved_sur_repo_dir()
    except Exception:
        repo_root = None
    if repo_root is not None:
        for key, relative_path in _HASHED_SURFACES.items():
            digest = _short_sha256(repo_root / relative_path)
            if digest:
                provenance[key] = digest

    if include_engine:
        try:
            # Imported lazily: runner imports config, and this keeps
            # provenance importable without pulling in the run machinery.
            from .runner import resolve_attractor_resolution

            resolution = resolve_attractor_resolution(config.attractor_checkout)
            provenance["engine_source"] = resolution.source
            provenance["engine_path"] = resolution.command[0]
        except Exception:
            # No usable engine is a real failure -- but it is run_round's
            # failure to report, loudly, with its own diagnosis. Provenance
            # just records less.
            pass

    return provenance


def provenance_differences(recorded: dict | None, current: dict | None) -> list[str]:
    """Human-readable differences between a run's harness and the current one.

    Compares only `_COMPARED_FIELDS` (the behavior-shaping surfaces). A field
    missing from EITHER side is skipped rather than reported as a difference:
    records written before this feature existed carry no harness at all, and
    reporting every absent key as a mismatch would be noise, not signal.

    Returns:
        One line per differing field; empty when they agree (or when there
        is nothing comparable).
    """
    if not recorded or not current:
        return []

    differences: list[str] = []
    for field in _COMPARED_FIELDS:
        was, now = recorded.get(field), current.get(field)
        if was and now and was != now:
            differences.append(f"{field}: run={was} current={now}")
    return differences


def describe_provenance(provenance: dict | None) -> str:
    """One-line summary for humans; explicit about an absent record."""
    if not provenance:
        return "not recorded (this round predates harness provenance)"
    return " ".join(f"{k}={v}" for k, v in sorted(provenance.items()))
