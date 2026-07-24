"""Gate triage -- grade a run's findings and persist the verdicts.

After a run reaches the approval gate, the synthesis stage has emitted
``<output_dir>/findings.json`` (the graph's contract):

    {"run_id": "...",
     "findings": [{"id", "title", "severity", "evidence_tier",
                   "confirmation", "repro", "sources"}, ...]}

The triage flow asks a human for a per-finding verdict (real / noise /
wont-fix -- ~30 seconds for a typical round) plus the gate verdict, and
persists both into that run's record in the ``rounds.jsonl`` ledger. This
is what makes the precision-at-gate metric measurable instead of
aspirational: after triage, "N/M graded real" is a fact in the ledger, and
observed-tier vs simulated-tier precision can be reported separately
(persona feelings are simulation -- they must not borrow the credibility
of machine-checked observations).

This module contains NO pipeline logic -- findings.json's schema is owned
by the graph (pipelines/simulated-user-research.dot); we read it and pass
it through.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from .runner import FINDINGS_ARTIFACT, ROUNDS_LEDGER

# The three triage verdicts (accepted answers; single-letter shortcuts too).
TRIAGE_VERDICTS = ("real", "noise", "wont-fix")
_VERDICT_SHORTCUTS = {
    "r": "real",
    "n": "noise",
    "w": "wont-fix",
    "real": "real",
    "noise": "noise",
    "wont-fix": "wont-fix",
    "wontfix": "wont-fix",
}

# Gate verdicts (what the human chose at/after the approval gate).
GATE_VERDICTS = ("approve", "revise", "end")
_GATE_SHORTCUTS = {
    "a": "approve",
    "r": "revise",
    "e": "end",
    "approve": "approve",
    "revise": "revise",
    "end": "end",
}


def load_findings(output_dir: str | Path) -> dict:
    """Load and structurally check ``findings.json`` from a round's output dir.

    Raises:
        FileNotFoundError: if findings.json does not exist (the run may
            predate the findings contract, or synthesis never completed).
        ValueError: if the file isn't valid JSON or lacks a findings list.
    """
    path = Path(output_dir).expanduser() / FINDINGS_ARTIFACT
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found -- either this round predates the findings.json "
            f"contract or synthesis never completed. Re-run the round to get a "
            f"triageable findings file."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: not valid JSON: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise ValueError(
            f'{path}: expected {{"run_id": ..., "findings": [...]}}, '
            f"got {type(data).__name__} without a findings list"
        )
    return data


def read_rounds(rounds_path: str | Path) -> list[dict]:
    """Read all parseable records from a ``rounds.jsonl`` ledger.

    Malformed lines are skipped (a corrupt line must not make the whole
    ledger unreadable); missing file returns an empty list.
    """
    path = Path(rounds_path).expanduser()
    if not path.is_file():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def latest_round(output_dir: str | Path) -> dict | None:
    """Return the most recent run record from a project's ledger, if any."""
    records = read_rounds(Path(output_dir).expanduser() / ROUNDS_LEDGER)
    return records[-1] if records else None


def _normalize_answer(raw: str, shortcuts: dict[str, str]) -> str | None:
    return shortcuts.get(raw.strip().lower())


def ask_gate_verdict(ask: Callable[[str], str]) -> str:
    """Prompt for the gate verdict via `ask` until a valid answer is given."""
    prompt = "gate verdict -- [a]pprove / [r]evise / [e]nd: "
    while True:
        verdict = _normalize_answer(ask(prompt), _GATE_SHORTCUTS)
        if verdict:
            return verdict
        print("  please answer a/approve, r/revise, or e/end")


def run_triage(findings: list[dict], ask: Callable[[str], str]) -> list[dict]:
    """Grade each finding via the `ask` callable; returns verdict records.

    `ask` is `input`-shaped (prompt -> answer string); injected so tests
    and non-TTY callers can script it. Invalid answers re-prompt.

    Returns:
        One ``{"id", "verdict"}`` record per finding, in order.
    """
    results: list[dict] = []
    for i, finding in enumerate(findings, start=1):
        fid = str(finding.get("id", f"finding-{i}"))
        title = str(finding.get("title", "(untitled)"))
        severity = str(finding.get("severity", "?"))
        tier = str(finding.get("evidence_tier", "?"))
        prompt = (
            f"[{i}/{len(findings)}] {fid} ({severity}, {tier}) {title}\n"
            f"  [r]eal / [n]oise / [w]ont-fix: "
        )
        while True:
            verdict = _normalize_answer(ask(prompt), _VERDICT_SHORTCUTS)
            if verdict:
                break
            print("  please answer r/real, n/noise, or w/wont-fix")
        results.append({"id": fid, "verdict": verdict})
    return results


def record_triage(
    rounds_path: str | Path,
    run_id: str,
    gate_verdict: str,
    triage: list[dict],
) -> dict:
    """Persist gate verdict + triage into the run's ledger record.

    Rewrites ``rounds.jsonl`` atomically (temp file + rename), updating
    ONLY the record whose run_id matches (the last one, if duplicated);
    every other line -- including unparseable ones -- is preserved
    byte-for-byte.

    Returns:
        The updated record.

    Raises:
        ValueError: if no record with `run_id` exists in the ledger.
    """
    path = Path(rounds_path).expanduser()
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []

    target_index: int | None = None
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("run_id") == run_id:
            target_index = i
            break

    if target_index is None:
        raise ValueError(f"no record with run_id {run_id!r} in {path}")

    record = json.loads(lines[target_index])
    record["gate"] = gate_verdict
    record["triage"] = triage
    lines[target_index] = json.dumps(record)

    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    return record


def _tier_bucket(finding: dict) -> str:
    tier = str(finding.get("evidence_tier", "")).strip().strip("[]").lower()
    if tier in ("observed", "simulated", "inferred"):
        return tier
    return "unknown"


def precision_summary(findings: list[dict], triage: list[dict]) -> str:
    """The precision-at-gate line: overall + per-evidence-tier breakdown.

    Observed-tier and simulated-tier precision are reported SEPARATELY --
    conflating them would let persona simulation borrow the credibility of
    machine-checked observations (the exact overclaim the evidence-tier
    vocabulary exists to prevent).
    """
    verdict_by_id = {t["id"]: t["verdict"] for t in triage}

    total = len(findings)
    real_total = 0
    per_tier: dict[str, list[int]] = {}  # tier -> [real, graded]
    for i, finding in enumerate(findings, start=1):
        fid = str(finding.get("id", f"finding-{i}"))
        verdict = verdict_by_id.get(fid)
        if verdict is None:
            continue
        tier = _tier_bucket(finding)
        bucket = per_tier.setdefault(tier, [0, 0])
        bucket[1] += 1
        if verdict == "real":
            bucket[0] += 1
            real_total += 1

    parts = [f"{real_total}/{total} graded real"]
    for tier in ("observed", "simulated", "inferred", "unknown"):
        if tier in per_tier:
            real, graded = per_tier[tier]
            parts.append(f"{tier} tier {real}/{graded}")
    return "precision at gate: " + " -- ".join(parts)
