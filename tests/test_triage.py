"""Tests for the gate-triage flow: findings loading, grading, persistence,
and the precision-at-gate summary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from amplifier_simulated_user_research.triage import (
    ask_gate_verdict,
    latest_round,
    load_findings,
    precision_summary,
    read_rounds,
    record_triage,
    run_triage,
)

_FINDINGS = [
    {
        "id": "F-001",
        "title": "Dead tap on consent decline",
        "severity": "P1",
        "evidence_tier": "OBSERVED",
        "confirmation": "REPRODUCED (2 sessions)",
        "repro": "1. open onboarding 2. tap Decline",
        "sources": ["persona-marisol.md"],
    },
    {
        "id": "F-002",
        "title": "Marisol would not adopt",
        "severity": "P2",
        "evidence_tier": "SIMULATED",
        "confirmation": "RAISED BY 1/3 PERSONAS",
        "repro": "n/a",
        "sources": ["persona-marisol.md"],
    },
    {
        "id": "F-003",
        "title": "Digest likely confuses first-time users",
        "severity": "P3",
        "evidence_tier": "INFERRED",
        "confirmation": "RELATED FINDING",
        "repro": "n/a",
        "sources": ["research-spec.md"],
    },
]


def _scripted_ask(answers: list[str]):
    """An input()-shaped callable fed from a list (mocked stdin)."""
    queue = list(answers)

    def ask(prompt: str) -> str:
        return queue.pop(0)

    return ask


def _write_findings(output_dir: Path, run_id: str = "r-20260723-120000") -> Path:
    path = output_dir / "findings.json"
    path.write_text(
        json.dumps({"run_id": run_id, "findings": _FINDINGS}), encoding="utf-8"
    )
    return path


def _write_rounds(output_dir: Path, run_ids: list[str]) -> Path:
    path = output_dir / "rounds.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        prior = None
        for run_id in run_ids:
            f.write(
                json.dumps(
                    {
                        "run_id": run_id,
                        "status": "gate_reached",
                        "gate_reached": True,
                        "prior_run_id": prior,
                        "gate": None,
                        "triage": None,
                    }
                )
                + "\n"
            )
            prior = run_id
    return path


class TestLoadFindings:
    def test_loads_valid_findings(self, tmp_path):
        _write_findings(tmp_path)
        doc = load_findings(tmp_path)
        assert doc["run_id"] == "r-20260723-120000"
        assert len(doc["findings"]) == 3

    def test_missing_file_raises_with_guidance(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="findings.json"):
            load_findings(tmp_path)

    def test_invalid_json_raises(self, tmp_path):
        (tmp_path / "findings.json").write_text("{broken", encoding="utf-8")
        with pytest.raises(ValueError, match="not valid JSON"):
            load_findings(tmp_path)

    def test_missing_findings_list_raises(self, tmp_path):
        (tmp_path / "findings.json").write_text('{"run_id": "x"}', encoding="utf-8")
        with pytest.raises(ValueError, match="findings list"):
            load_findings(tmp_path)


class TestRunTriage:
    def test_grades_each_finding_in_order(self):
        triage = run_triage(_FINDINGS, _scripted_ask(["r", "n", "w"]))
        assert triage == [
            {"id": "F-001", "verdict": "real"},
            {"id": "F-002", "verdict": "noise"},
            {"id": "F-003", "verdict": "wont-fix"},
        ]

    def test_accepts_full_words_and_reprompts_on_invalid(self):
        triage = run_triage(
            _FINDINGS, _scripted_ask(["real", "??", "noise", "WONT-FIX"])
        )
        assert [t["verdict"] for t in triage] == ["real", "noise", "wont-fix"]

    def test_gate_verdict_shortcuts_and_reprompt(self):
        assert ask_gate_verdict(_scripted_ask(["a"])) == "approve"
        assert ask_gate_verdict(_scripted_ask(["bogus", "revise"])) == "revise"


class TestRecordTriage:
    def test_persists_gate_and_triage_into_matching_record(self, tmp_path):
        rounds_path = _write_rounds(tmp_path, ["r-20260723-120000"])
        triage = [{"id": "F-001", "verdict": "real"}]

        updated = record_triage(rounds_path, "r-20260723-120000", "approve", triage)

        assert updated["gate"] == "approve"
        assert updated["triage"] == triage
        on_disk = read_rounds(rounds_path)
        assert on_disk[0]["gate"] == "approve"
        assert on_disk[0]["triage"] == triage

    def test_updates_only_the_matching_line(self, tmp_path):
        rounds_path = _write_rounds(
            tmp_path, ["r-20260723-110000", "r-20260723-120000"]
        )

        record_triage(rounds_path, "r-20260723-120000", "approve", [])

        records = read_rounds(rounds_path)
        assert records[0]["gate"] is None  # first run untouched
        assert records[1]["gate"] == "approve"
        assert records[1]["prior_run_id"] == "r-20260723-110000"  # preserved

    def test_unknown_run_id_raises(self, tmp_path):
        rounds_path = _write_rounds(tmp_path, ["r-20260723-120000"])
        with pytest.raises(ValueError, match="no record with run_id"):
            record_triage(rounds_path, "r-19990101-000000", "approve", [])

    def test_preserves_unparseable_lines(self, tmp_path):
        rounds_path = _write_rounds(tmp_path, ["r-20260723-120000"])
        with open(rounds_path, "a", encoding="utf-8") as f:
            f.write("{corrupt line\n")

        record_triage(rounds_path, "r-20260723-120000", "end", [])

        raw_lines = rounds_path.read_text(encoding="utf-8").splitlines()
        assert raw_lines[-1] == "{corrupt line"


class TestLatestRound:
    def test_returns_last_record(self, tmp_path):
        _write_rounds(tmp_path, ["r-20260723-110000", "r-20260723-120000"])
        record = latest_round(tmp_path)
        assert record is not None
        assert record["run_id"] == "r-20260723-120000"

    def test_returns_none_without_ledger(self, tmp_path):
        assert latest_round(tmp_path) is None


class TestPrecisionSummary:
    def test_reports_overall_and_per_tier_separately(self):
        triage = [
            {"id": "F-001", "verdict": "real"},  # observed
            {"id": "F-002", "verdict": "noise"},  # simulated
            {"id": "F-003", "verdict": "real"},  # inferred
        ]
        summary = precision_summary(_FINDINGS, triage)
        assert "2/3 graded real" in summary
        assert "observed tier 1/1" in summary
        assert "simulated tier 0/1" in summary
        assert "inferred tier 1/1" in summary

    def test_ungraded_findings_counted_in_total_only(self):
        triage = [{"id": "F-001", "verdict": "real"}]
        summary = precision_summary(_FINDINGS, triage)
        assert "1/3 graded real" in summary
        assert "observed tier 1/1" in summary
        assert "simulated" not in summary  # nothing graded in that tier
