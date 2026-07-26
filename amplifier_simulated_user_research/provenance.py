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
from dataclasses import dataclass
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


# --- Installed-build staleness -------------------------------------------
#
# WHY THIS EXISTS (a second incident, half a loop closed by the machinery
# above): `harness_provenance` records, AFTER a round runs, which build
# produced it. That answers "was this finding interpretable" in hindsight.
# It does not answer the question that matters BEFORE spending an hour and
# real model spend: "is the build about to run even the one I think it is?"
#
# Two real incidents share this shape:
#   1. Round 6 ran on an installed build that predated the click-discipline
#      fix -- found by manually grepping the installed wrapper for the
#      marker string. That grep is what harness_provenance automated.
#   2. A later fix landed as a merged PR; it was reported as shipped. The
#      INSTALLED build still did not contain it -- merging is not the same
#      as reinstalling. Had a round run in between, it would have
#      reproduced a defect that was already fixed on `main`.
#
# SOURCE OF TRUTH, chosen deliberately: a local git checkout of this same
# project. Comparing against the GitHub remote was considered and rejected:
# it requires network, can fail or hang for reasons that have nothing to do
# with staleness, and a check that can fail for unrelated reasons trains
# people to ignore it (the same lesson PRINCIPLES.md #4 draws about
# preflight checks generally).
#
# WHICH checkout, in priority order:
#
#   1. `config.sur_repo_dir`, when a config supplies it. The operator has
#      EXPLICITLY declared which checkout this run belongs to -- that is
#      strictly more authoritative than any search heuristic. This is not a
#      refinement; it is the whole check. A first version of this feature
#      only walked upward from the cwd, and it silently returned
#      "undetermined" in the exact workflow both incidents came from: rounds
#      are launched from the WORKSPACE ROOT with `--config`, where the
#      checkout is a DESCENDANT of the cwd, so walking up never reaches it
#      -- while the config named its absolute path the whole time.
#   2. Otherwise, a bounded upward walk from the current working directory
#      (the convention `git` itself uses). This is the `doctor` with no
#      `--config` case: no declaration to honor, so fall back to "the
#      checkout you are standing in."
#
# A config-declared directory that does not QUALIFY (below) yields
# "undetermined" -- deliberately WITHOUT falling back to the walk. The
# operator said this run belongs to checkout X; silently grading it against
# some unrelated checkout Y found up the tree would be a confident verdict
# about the wrong repository, which is worse than no verdict.
#
# QUALIFYING as a checkout requires BOTH a `.git` marker and the two hashed
# surfaces. A plain copy of the bundled tree (e.g. another installed build
# sitting on disk) is deliberately not mistaken for a development checkout.
#
# THE INSTALLED SIDE is always `config._package_repo_root()` -- the tree
# belonging to the Python package that is actually executing (the wheel's
# `_bundled/` for a `uv tool install`, the source tree under `uv run`).
# It is emphatically NOT `config.resolved_sur_repo_dir()`: that method
# returns the config's own `sur_repo_dir` override when set, so using it
# compared the declared checkout against ITSELF and could only ever report
# "current". The running build and the checkout are two different things,
# and this check exists precisely to compare them.
#
# When no checkout can be established -- the common case for a plain
# `uv tool install` invoked with no config and no nearby clone -- the
# honest answer is "undetermined," never a fabricated "current." A check
# that cannot compare anything must say so, not manufacture agreement.
#
# LIMITS, stated so nobody reads more into a "current" than it supports:
# this compares the two PROMPT-SHAPING surfaces, which is what governs what
# a finding says. It is blind to changes that live only in this package's
# Python (orchestration, ledger, triage) -- a Python-only release can leave
# the installed CLI behind while both surfaces still match, and this
# reports "current". Note also that when `sur_repo_dir` names the checkout,
# the ROUND reads its prompts from the checkout, not from the installed
# `_bundled/` copies; a "stale" verdict there is therefore evidence that
# the CLI was never reinstalled (so its Python IS behind), rather than
# proof that this round's prompts were wrong. Both readings are actionable;
# neither is "your findings are invalid."

# Bounded upward walk (mirrors how `git` finds a repo root): far more than
# any real project nesting depth, but never unbounded.
_CHECKOUT_SEARCH_MAX_LEVELS = 8


def _qualifies_as_checkout(path: Path) -> bool:
    """True when `path` is a git checkout of this project.

    Requires BOTH a `.git` entry and every `_HASHED_SURFACES` file: `.git`
    is what distinguishes a development checkout from a plain copy of the
    same two files (e.g. another installed build's `_bundled/` tree), which
    must never be mistaken for the source of truth. Never raises -- an
    unreadable path is simply not a checkout.
    """
    try:
        return (path / ".git").exists() and all(
            (path / relative_path).is_file()
            for relative_path in _HASHED_SURFACES.values()
        )
    except OSError:
        return False


def _discover_local_checkout(
    start: Path, max_levels: int = _CHECKOUT_SEARCH_MAX_LEVELS
) -> Path | None:
    """Walk upward from `start` for a git checkout of this project.

    The FALLBACK source of truth, used only when no config declares one
    (see this section's header comment: a config's `sur_repo_dir` is more
    authoritative, and an upward walk cannot find a checkout that is a
    DESCENDANT of the working directory -- the shape of every real round).

    Returns:
        The checkout's root path, or None if nothing qualifies within
        `max_levels` steps (never raises; a missing/unreadable directory
        along the way is just another non-match).
    """
    try:
        current = start.resolve()
    except OSError:
        return None

    for _ in range(max_levels):
        if _qualifies_as_checkout(current):
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


@dataclass(frozen=True)
class StalenessResult:
    """Result of comparing the installed build against a local git checkout.

    Attributes:
        status: one of --
            "current"      a checkout was established and its prompt-shaping
                            surfaces match the installed build (or the
                            installed build IS that checkout, e.g. running
                            via `uv run` from inside a source tree).
            "stale"        a checkout was established and at least one
                            surface differs -- the installed build does not
                            reflect what is on disk in the checkout.
            "undetermined" no checkout could be established: none declared
                            and none found nearby, or the declared one does
                            not qualify. NOT a failure, and never a
                            fabricated "current".
        checkout_path: the established checkout's root, or None when
            undetermined.
        checkout_source: how it was established -- "config sur_repo_dir"
            (the operator declared it) or "directory walk" (found by
            searching upward from the working directory). None when
            undetermined.
        differences: human-readable `field: installed=X checkout=Y` lines,
            one per differing surface. Empty unless status == "stale".
        detail: one-line human-facing summary.
    """

    status: str
    checkout_path: str | None
    checkout_source: str | None
    differences: tuple[str, ...]
    detail: str


def _undetermined(detail: str) -> StalenessResult:
    """An honest non-answer: no verdict, and explicitly no comparison made."""
    return StalenessResult("undetermined", None, None, (), detail)


def check_installed_build_staleness(
    config: RoundConfig | None = None, *, start: Path | None = None
) -> StalenessResult:
    """Compare the installed build's prompt-shaping surfaces to a local checkout.

    The installed side is always the running package's own tree
    (`config._package_repo_root`). The checkout side is the config's
    declared `sur_repo_dir` when there is one, else an upward walk from
    `start`. See this section's header comment for why that priority is
    load-bearing rather than cosmetic.

    Args:
        config: Optional RoundConfig. Its `sur_repo_dir`, when set, is the
            authoritative source of truth -- the operator has declared
            which checkout this run belongs to.
        start: Where to begin the FALLBACK upward search when no config
            declares a repo dir. Defaults to the current working directory
            -- override in tests.

    Returns:
        A StalenessResult. Never raises: every failure mode (unresolvable
        package root, unreadable files, no checkout established) degrades
        to `status="undetermined"` rather than fabricating a verdict.
    """
    # Local import: config._package_repo_root is a "private" helper this
    # module already treats as internal API (see harness_provenance's use
    # of config.resolved_sur_repo_dir, which wraps the same function).
    from .config import _package_repo_root

    # THE INSTALLED SIDE: the tree belonging to the package that is
    # actually executing -- never config.resolved_sur_repo_dir(), which
    # returns the config's own override and would compare the declared
    # checkout against itself.
    try:
        installed_root = _package_repo_root().resolve()
    except OSError:
        return _undetermined(
            "could not resolve the running package's own repo root -- "
            "staleness cannot be checked"
        )

    declared = config.sur_repo_dir if config else None
    if declared:
        try:
            checkout = Path(declared).expanduser().resolve()
        except OSError:
            return _undetermined(
                f"config declares sur_repo_dir={declared} but it could not "
                f"be resolved -- cannot verify the installed build against it"
            )
        if not _qualifies_as_checkout(checkout):
            # Deliberately no fallback to the walk: the operator said this
            # run belongs to THIS directory, and grading it against some
            # other checkout found up the tree would be a confident verdict
            # about the wrong repository.
            return _undetermined(
                f"config declares sur_repo_dir={checkout}, but that is not a "
                f"git checkout of this project (needs a .git marker plus "
                f"scripts/run_browser_node.py and the pipeline .dot) -- "
                f"cannot verify the installed build against it"
            )
        checkout_source = "config sur_repo_dir"
    else:
        found = _discover_local_checkout(start or Path.cwd())
        if found is None:
            return _undetermined(
                "no local git checkout of this project found near the current "
                "directory, and no config declared one -- cannot verify the "
                "installed build against source (expected for a plain "
                "`uv tool install`; not a failure)"
            )
        checkout = found
        checkout_source = "directory walk"

    if checkout == installed_root:
        return StalenessResult(
            "current",
            str(checkout),
            checkout_source,
            (),
            f"running directly from the checkout at {checkout}",
        )

    differences: list[str] = []
    for key, relative_path in _HASHED_SURFACES.items():
        installed_hash = _short_sha256(installed_root / relative_path)
        checkout_hash = _short_sha256(checkout / relative_path)
        if installed_hash and checkout_hash and installed_hash != checkout_hash:
            differences.append(
                f"{key}: installed={installed_hash} checkout={checkout_hash}"
            )

    if differences:
        return StalenessResult(
            "stale",
            str(checkout),
            checkout_source,
            tuple(differences),
            f"installed build ({installed_root}) differs from the checkout at "
            f"{checkout} ({'; '.join(differences)}) -- merging a fix is not "
            f"the same as shipping it; reinstall before trusting a run "
            f"against this checkout",
        )
    return StalenessResult(
        "current",
        str(checkout),
        checkout_source,
        (),
        f"matches the checkout at {checkout}",
    )
