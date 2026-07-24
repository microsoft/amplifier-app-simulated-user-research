#!/usr/bin/env python3
"""validate_artifact.py -- content-aware artifact contracts for the
simulated-user-research pipeline's check_*/verify_* gate nodes.

WHY THIS EXISTS (read before editing):

Three hardening councils independently found the same defect: every
check_*/verify_* gate in simulated-user-research.dot judged an artifact by
BYTE COUNT alone (``test -f X && wc -c >= N``). A byte count is a length
check wearing a completeness costume -- the showcase run's
capture-notes.md was 1,800 bytes (comfortably over its 800-byte floor),
documented 4 of 11 screens, and ended with the literal line
``## In Progress -- Additional Screens (to be completed)``. It passed
every gate, was re-grandfathered by the identical resume guard on every
later run (the file existed and was "big enough", so check_capture kept
saying "done" forever), and synthesis then certified
``## Missing inputs\\nNone -- all expected inputs were present.`` A
partial artifact above the floor could never heal -- it entombed itself.

THE FIX IS THE CONTRACT, NOT THE FLOOR: this script replaces the inline
``wc -c`` content judgment in both check_* (skip-guard) and verify_*
(quality-gate) tool_commands with ONE shared, content-aware contract per
artifact type. Both call sites invoke the SAME script with the SAME
arguments -- that symmetry is what fixes the entombing defect: a partial
artifact now FAILS check_*'s skip-guard too (not just verify_*'s quality
gate), so the stage is not skipped, it re-runs, and it heals via the
existing partial-draft-resume mechanism already built into
run_browser_node.py (build_capture_instruction/build_persona_instruction
inject the existing partial content back into the next attempt's prompt
as a continuation point, not a fresh start).

CONTRACT PER ARTIFACT TYPE (see the individual validate_* functions for
the authoritative checks; this is a summary):

  ALL types:
    - a cheap byte-size floor as the FIRST, fast-fail check (see MIN_SIZE)
    - no placeholder markers anywhere in the body: "to be completed",
      "(in progress)", or a bare "TODO" token
    - a "Run ID: <value>" field whose value is non-empty and not the
      literal word "unknown" (case-insensitive) -- and, when --run-id is
      given, matches this run's run_id exactly (catches a stale artifact
      left over from a different/earlier run being silently reused)

  capture:
    - "## Screens Captured" and "## Observed Issues" section headers
    - every screenshot filename cited in the body -- either
      "screens/<file>.(png|jpg|jpeg)" or a bare "<file>.(png|jpg|jpeg)"
      token (the prompt asks for "the specific screenshot filename", not
      a mandated "screens/" prefix, so both forms are conforming) --
      must actually exist on disk under --screens-dir
    - --screens-dir must contain >= 8 real files (2 viewports x >= 4
      screens -- a real walkthrough, not a token gesture)

  persona:
    - the enforced report skeleton's section headers (H1, Persona,
      Friction Log, In-Character Reactions, Delights, Bug Table,
      One Change, Verdict)
    - the Bug Table must have at least one real data row, OR the report
      must say so honestly with a "No bugs observed" line -- an empty
      table with neither is indistinguishable from a session that died
      before writing anything real

  review:
    - >= 3 "## "-prefixed section headers (a bare skeleton with no real
      findings reads as 1-2 headers and a lot of white space)

  spec:
    - the spec header-block section headers (Verdict Summary, Findings
      At a Glance, Summary, Detailed Findings, Prioritized Changes, Copy
      Fixes, Parked Items, Sources, Provenance)
    - a valid, non-empty findings.json SIBLING file (--findings-json):
      valid JSON, a "run_id" matching this run, and a "findings" list
      where every entry has the required keys with values drawn from the
      controlled vocabularies (severity / evidence_tier / confirmation)

WHAT THIS SCRIPT DELIBERATELY DOES NOT DO: it does not judge whether the
PROSE is any good (that is still the model's job, and ultimately a
human's at the gate). It only judges whether the artifact is the kind of
thing that COULD be good -- the right shape, the right sections, no
lingering placeholders, cited evidence that actually exists on disk. That
is the ceiling a byte-floor check can never reach and the floor a human
reviewer should never have to re-derive by eye every round.

USAGE (called by the .dot's check_*/verify_* tool_command bash snippets,
never invoked directly by a human in normal operation):

    python3 validate_artifact.py <type> <path> \\
        [--screens-dir DIR] [--run-id RUN_ID] \\
        [--findings-json PATH] [--min-size N]

    <type> is one of: capture, persona, review, spec

EXIT CODE / STDOUT CONTRACT: exit 0 = contract satisfied, one "ok: ..."
line on stdout. Exit 1 = contract violated, one "invalid: <reason>" line
on stdout (and mirrored to stderr so it survives into the tool node's
output.txt log even if a caller only captures one stream). The calling
.dot bash snippet wraps this in ``if ...; then printf done/ok; else ...
fi`` -- this script's own stdout is diagnostic prose for humans reading
logs, never the pipeline's routing token (context.tool.last_line always
comes from the wrapping bash's own printf, keeping the .dot's edge
conditions readable and this script freely revisable without touching
edge syntax).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import NoReturn

# ---------------------------------------------------------------------------
# Shared vocabulary / floors (kept here, not duplicated in the .dot or the
# synthesis prompt in run_browser_node.py -- if you change one of these
# controlled vocabularies, findings.json validation and the prompt that
# produces it will silently drift unless you update both places).
# ---------------------------------------------------------------------------

MIN_SIZE = {"capture": 800, "persona": 1500, "review": 1500, "spec": 1500}

SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
EVIDENCE_TIERS = {"OBSERVED", "SIMULATED", "INFERRED"}
CONFIRMATIONS = {"REPRODUCED", "OBSERVED_SINGLE", "RAISED_BY_PERSONAS", "RELATED"}

_PLACEHOLDER_PATTERNS = [
    (re.compile(r"to be completed", re.I), "placeholder text 'to be completed'"),
    (re.compile(r"\(in progress\)", re.I), "placeholder marker '(in progress)'"),
    (re.compile(r"\btodo\b", re.I), "placeholder marker 'TODO'"),
]

# Accepts EITHER an explicit "screens/<file>" relative reference OR a bare
# "<file>.png|jpg|jpeg" token (no directory prefix). The capture prompt
# (run_browser_node.py's build_capture_instruction) asks the model to cite
# "the specific screenshot filename" -- it does not mandate the "screens/"
# prefix, so a bare backticked filename like `01-consent-mobile.png` is a
# CONFORMING citation, not a contract violation. Matching only the
# "screens/"-prefixed form here was a validator/prompt mismatch that caused
# a real, complete, well-evidenced artifact (22/22 cited files present on
# disk) to be rejected 3x and hard-stop the pipeline. '/' is deliberately
# excluded from the character class so "outputs/screens/01-x.png" and
# "some/other/01-x.png" both resolve to their trailing filename segment;
# existence is checked against that basename under --screens-dir regardless
# of which form was cited (see validate_capture below).
_SCREENSHOT_REF_RE = re.compile(r"(?:screens/)?[\w][\w.\-]*\.(?:png|jpg|jpeg)", re.I)

PERSONA_REQUIRED_HEADERS = [
    "## Persona",
    "## Friction Log",
    "## In-Character Reactions",
    "## Delights",
    "## Bug Table",
    "## One Change",
    "## Verdict",
]

CAPTURE_REQUIRED_HEADERS = [
    "## Screens Captured",
    "## Observed Issues",
]

# NOTE: "## Detailed Findings" (not "## Findings") is deliberate -- if this
# were just "## Findings", the "## Findings At a Glance" heading's own text
# would satisfy a naive startswith() check for BOTH headings and the
# detailed section could be silently missing. Keep these two headings
# textually non-overlapping; see check_headers()'s docstring.
SPEC_REQUIRED_HEADERS = [
    "## Verdict Summary",
    "## Findings At a Glance",
    "## Summary",
    "## Detailed Findings",
    "## Prioritized Changes",
    "## Copy Fixes",
    "## Parked Items",
    "## Sources",
    "## Provenance",
]

FINDING_REQUIRED_KEYS = {
    "id",
    "title",
    "severity",
    "evidence_tier",
    "confirmation",
    "repro",
    "sources",
}


def fail(reason: str) -> NoReturn:
    """Print the one-line diagnostic to stdout+stderr and exit 1."""
    print(f"invalid: {reason}", file=sys.stdout)
    print(f"invalid: {reason}", file=sys.stderr)
    sys.exit(1)


def ok(reason: str = "artifact satisfies its contract") -> NoReturn:
    print(f"ok: {reason}", file=sys.stdout)
    sys.exit(0)


def check_min_size(path: str, floor: int) -> None:
    """Cheap FIRST check -- fail fast on a missing/undersized file before
    doing any of the more expensive content parsing below."""
    try:
        size = os.path.getsize(path)
    except OSError:
        fail(f"{path} does not exist")
    if size < floor:
        fail(f"{path} is {size} bytes, below the {floor}-byte floor")


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def check_placeholders(text: str) -> None:
    for pattern, label in _PLACEHOLDER_PATTERNS:
        if pattern.search(text):
            fail(f"contains {label} -- this artifact is not finished")


def check_run_id(text: str, expected_run_id: str | None) -> None:
    m = re.search(r"run id:\s*(.+)", text, re.I)
    if not m:
        fail("no 'Run ID:' field found in the artifact header")
    # Strip trailing markdown emphasis markers (**bold**, _italic_, `code`)
    # and punctuation so "Run ID: **r-20260723**" and "Run ID: r-20260723."
    # both extract cleanly to "r-20260723".
    value = m.group(1).strip()
    value = re.sub(r"^[*_`]+|[*_`.\s]+$", "", value).strip()
    if not value or value.lower() == "unknown":
        fail(f"Run ID field is {value!r} -- placeholder/unknown Run ID")
    if expected_run_id and value != expected_run_id:
        fail(
            f"Run ID field is {value!r} but this run's run_id is "
            f"{expected_run_id!r} -- looks like a stale artifact left over "
            "from a different run"
        )


def check_headers(text: str, required_headers: list[str]) -> None:
    """Each required heading must PREFIX-match some stripped line in the
    body (not exact-match) -- several required headings carry an
    explanatory suffix by design (e.g. "## Verdict (simulated, per this
    persona's stated bar)"), and a prefix check tolerates that without
    hardcoding the exact suffix text here (which would make the prompt
    and the validator fragile-brittle against each other).

    CAUTION when adding new required headers: if heading A is a strict
    text-prefix of heading B (e.g. "## Findings" is a prefix of
    "## Findings At a Glance"), a document containing ONLY heading B would
    incorrectly satisfy the check for heading A too. Keep required
    headings textually non-overlapping (see SPEC_REQUIRED_HEADERS's
    "## Detailed Findings" comment for the concrete case this bit us).
    """
    lines = [ln.strip() for ln in text.splitlines()]
    missing = [h for h in required_headers if not any(ln.startswith(h) for ln in lines)]
    if missing:
        fail(f"missing required section header(s): {', '.join(missing)}")


def count_headers(text: str, prefix: str = "## ") -> int:
    return sum(1 for ln in text.splitlines() if ln.strip().startswith(prefix))


def extract_section(text: str, heading_prefix: str) -> str:
    """Return the body text between a line starting with heading_prefix
    and the next "## "-level heading (or EOF). Empty string if not found."""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith(heading_prefix):
            start = i + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start, len(lines)):
        if lines[j].strip().startswith("## ") and not lines[j].strip().startswith(
            heading_prefix
        ):
            end = j
            break
    return "\n".join(lines[start:end])


# ---------------------------------------------------------------------------
# Per-type contracts
# ---------------------------------------------------------------------------


def validate_capture(path: str, screens_dir: str | None, run_id: str | None) -> None:
    text = read_text(path)
    check_placeholders(text)
    check_run_id(text, run_id)
    check_headers(text, CAPTURE_REQUIRED_HEADERS)

    if not screens_dir:
        fail("--screens-dir is required for type=capture")
    if not os.path.isdir(screens_dir):
        fail(f"screens directory {screens_dir} does not exist")

    real_files = {
        f
        for f in os.listdir(screens_dir)
        if not f.startswith(".") and os.path.isfile(os.path.join(screens_dir, f))
    }
    if len(real_files) < 8:
        fail(
            f"screens/ contains only {len(real_files)} file(s); a real "
            "multi-screen walkthrough needs at least 8 (>= 4 screens x 2 "
            "viewports)"
        )

    # Citations may appear as either "screens/<file>" or a bare "<file>"
    # token (see _SCREENSHOT_REF_RE's comment) -- normalize both forms to
    # just the filename (basename) and check THAT exists directly under
    # --screens-dir. This is deliberately independent of whichever prefix
    # form the model happened to cite: existence on disk is what matters,
    # not which spelling of the path the prose used.
    cited_raw = set(_SCREENSHOT_REF_RE.findall(text))
    cited = {os.path.basename(c) for c in cited_raw}
    missing_files = [
        c for c in sorted(cited) if not os.path.isfile(os.path.join(screens_dir, c))
    ]
    if missing_files:
        fail(
            "cites screenshot file(s) that do not exist on disk: "
            + ", ".join(missing_files)
        )
    if not cited:
        fail(
            "cites zero screens/*.png|jpg filenames in the body -- a real "
            "walkthrough report references the screenshots it took"
        )


def validate_persona(path: str, run_id: str | None) -> None:
    text = read_text(path)
    check_placeholders(text)
    check_run_id(text, run_id)

    if not re.search(r"^#\s+\S", text, re.M):
        fail("missing an H1 title line (name + Run ID + date)")

    check_headers(text, PERSONA_REQUIRED_HEADERS)

    bug_section = extract_section(text, "## Bug Table")
    table_rows = [ln for ln in bug_section.splitlines() if ln.strip().startswith("|")]
    # A markdown table has a header row + a separator row before any real
    # data row -- fewer than 3 "|"-prefixed lines means no data row exists.
    has_data_row = len(table_rows) >= 3
    has_no_bugs_line = bool(re.search(r"no bugs observed", bug_section, re.I))
    if not (has_data_row or has_no_bugs_line):
        fail(
            "Bug Table has no data rows and no explicit 'No bugs observed' "
            "line -- can't tell whether this session found nothing or died "
            "before writing anything real"
        )


def validate_review(path: str, run_id: str | None) -> None:
    text = read_text(path)
    check_placeholders(text)
    check_run_id(text, run_id)
    n = count_headers(text)
    if n < 3:
        fail(f"only {n} '## '-level section header(s) found, need >= 3")


def validate_spec(
    path: str, run_id: str | None, findings_json_path: str | None
) -> None:
    text = read_text(path)
    check_placeholders(text)
    check_run_id(text, run_id)
    check_headers(text, SPEC_REQUIRED_HEADERS)

    if not findings_json_path:
        fail("--findings-json is required for type=spec")
    if not os.path.isfile(findings_json_path):
        fail(
            f"{findings_json_path} does not exist -- synthesis must write "
            "this JSON sibling alongside the spec, not just the markdown"
        )
    try:
        with open(findings_json_path, "r", encoding="utf-8") as f:
            raw = f.read()
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        fail(f"{findings_json_path} is not valid JSON: {e}")

    if not isinstance(data, dict):
        fail(f"{findings_json_path} must contain a JSON object at the top level")

    data_run_id = data.get("run_id")
    if not data_run_id:
        fail(f"{findings_json_path} is missing a non-empty 'run_id' field")
    if run_id and data_run_id != run_id:
        fail(
            f"{findings_json_path}'s run_id {data_run_id!r} does not match "
            f"this run's run_id {run_id!r} -- stale findings.json"
        )

    findings = data.get("findings")
    if not isinstance(findings, list) or len(findings) < 1:
        fail(f"{findings_json_path} must have a non-empty 'findings' list")

    for i, finding in enumerate(findings):
        label = f"findings[{i}]"
        if not isinstance(finding, dict):
            fail(f"{label} is not a JSON object")
        fid = finding.get("id", "?")
        label = f"findings[{i}] (id={fid})"

        missing_keys = FINDING_REQUIRED_KEYS - set(finding)
        if missing_keys:
            fail(f"{label} is missing key(s): {', '.join(sorted(missing_keys))}")

        if finding.get("severity") not in SEVERITIES:
            fail(
                f"{label} has invalid severity {finding.get('severity')!r} "
                f"(must be one of {sorted(SEVERITIES)})"
            )
        if finding.get("evidence_tier") not in EVIDENCE_TIERS:
            fail(
                f"{label} has invalid evidence_tier "
                f"{finding.get('evidence_tier')!r} (must be one of "
                f"{sorted(EVIDENCE_TIERS)})"
            )
        if finding.get("confirmation") not in CONFIRMATIONS:
            fail(
                f"{label} has invalid confirmation "
                f"{finding.get('confirmation')!r} (must be one of "
                f"{sorted(CONFIRMATIONS)})"
            )
        if not isinstance(finding.get("repro"), list):
            fail(
                f"{label} 'repro' must be a list (may be empty for non-OBSERVED tiers)"
            )
        sources = finding.get("sources")
        if not isinstance(sources, list) or len(sources) < 1:
            fail(f"{label} 'sources' must be a non-empty list")
        if finding["evidence_tier"] == "OBSERVED" and len(finding["repro"]) < 1:
            fail(f"{label} is evidence_tier OBSERVED but has zero repro steps")


# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "artifact_type", choices=["capture", "persona", "review", "spec"]
    )
    parser.add_argument("path")
    parser.add_argument("--screens-dir", default=None, help="required for type=capture")
    parser.add_argument(
        "--run-id", default=None, help="this run's run_id, for staleness checks"
    )
    parser.add_argument("--findings-json", default=None, help="required for type=spec")
    parser.add_argument(
        "--min-size",
        type=int,
        default=None,
        help="override the type's default byte floor (rarely needed)",
    )
    args = parser.parse_args()

    floor = args.min_size if args.min_size is not None else MIN_SIZE[args.artifact_type]
    # Shared cheap FIRST check for every type -- fail fast before any of the
    # heavier per-type parsing below runs.
    check_min_size(args.path, floor)

    if args.artifact_type == "capture":
        validate_capture(args.path, args.screens_dir, args.run_id)
    elif args.artifact_type == "persona":
        validate_persona(args.path, args.run_id)
    elif args.artifact_type == "review":
        validate_review(args.path, args.run_id)
    elif args.artifact_type == "spec":
        validate_spec(args.path, args.run_id, args.findings_json)

    ok()


if __name__ == "__main__":
    main()
