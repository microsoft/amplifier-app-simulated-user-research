"""Tests for the amplifier-simulated-user-research CLI surface: init scaffolding, gate messaging,
sentinel rejection, the stop/fail alias, and the triage command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import yaml

from amplifier_simulated_user_research.cli import main
from amplifier_simulated_user_research.runner import RoundResult


def _valid_config_yaml(tmp_path: Path) -> Path:
    personas_dir = tmp_path / "personas"
    personas_dir.mkdir(exist_ok=True)
    config = {
        "target_url": "http://127.0.0.1:8892",
        "seed_command": "true",
        "seed_cwd": str(tmp_path),
        "personas_dir": str(personas_dir),
        "output_dir": str(tmp_path / "output"),
        "app_source_hint": str(tmp_path),
        "personas": ["marisol", "dev", "ken"],
        "api_key": "test-key",
        "sur_repo_dir": str(tmp_path),
    }
    path = tmp_path / "project.yaml"
    path.write_text(cast(str, yaml.safe_dump(config)), encoding="utf-8")
    return path


class TestInit:
    def test_scaffolds_config_personas_and_template(self, tmp_path, capsys):
        rc = main(["init", "--dir", str(tmp_path / "round")])
        assert rc == 0

        target = tmp_path / "round"
        assert (target / "project.yaml").is_file()
        for name in ("marisol.md", "dev.md", "ken.md", "_TEMPLATE.md"):
            assert (target / "personas" / name).is_file()

        out = capsys.readouterr().out
        assert "PERSONAS ARE PRODUCT-SPECIFIC" in out
        assert "1." in out and "2." in out  # numbered next steps
        assert "amplifier-simulated-user-research triage" in out

    def test_starter_config_is_rejected_by_run(self, tmp_path, capsys):
        main(["init", "--dir", str(tmp_path / "round")])
        rc = main(["run", "--config", str(tmp_path / "round" / "project.yaml")])
        assert rc == 1
        err = capsys.readouterr().err
        assert "edit project.yaml before running" in err


class TestRunGateMessaging:
    def _patch_run_round(self, monkeypatch, tmp_path):
        output_dir = tmp_path / "output"
        output_dir.mkdir(exist_ok=True)
        result = RoundResult(
            run_id="r-20260723-120000",
            status="gate_reached",
            exit_code=1,
            attractor_status="fail",
            gate_reached=True,
            artifacts={"research-spec.md": output_dir / "research-spec.md"},
            logs_dir=output_dir / ".attractor-logs" / "r-20260723-120000",
            rounds_path=output_dir / "rounds.jsonl",
        )
        monkeypatch.setattr(
            "amplifier_simulated_user_research.cli.run_round",
            lambda config, on_human_gate, timeout_s: result,
        )

    def test_gate_reached_speaks_human(self, tmp_path, monkeypatch, capsys):
        config_path = _valid_config_yaml(tmp_path)
        self._patch_run_round(monkeypatch, tmp_path)

        rc = main(["run", "--config", str(config_path)])

        assert rc == 0
        out = capsys.readouterr().out
        assert "research spec ready ->" in out
        assert "re-run this command in a terminal to answer the gate" in out
        assert "amplifier-simulated-user-research triage" in out
        # the old status-line dump is gone from the gate path
        assert "attractor_status=" not in out

    def test_deprecated_fail_alias_notes_on_stderr(self, tmp_path, monkeypatch, capsys):
        config_path = _valid_config_yaml(tmp_path)
        self._patch_run_round(monkeypatch, tmp_path)

        rc = main(["run", "--config", str(config_path), "--on-human-gate", "fail"])

        assert rc == 0
        err = capsys.readouterr().err
        assert "deprecated alias" in err

    def test_stop_is_accepted_without_note(self, tmp_path, monkeypatch, capsys):
        config_path = _valid_config_yaml(tmp_path)
        self._patch_run_round(monkeypatch, tmp_path)

        rc = main(["run", "--config", str(config_path), "--on-human-gate", "stop"])

        assert rc == 0
        assert "deprecated" not in capsys.readouterr().err


class TestTriageCommand:
    def _seed_round(self, tmp_path: Path, run_id: str = "r-20260723-120000") -> Path:
        """Create output_dir with rounds.jsonl + findings.json for one run."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(exist_ok=True)
        (output_dir / "rounds.jsonl").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "status": "gate_reached",
                    "gate_reached": True,
                    "gate": None,
                    "triage": None,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (output_dir / "findings.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "findings": [
                        {
                            "id": "F-001",
                            "title": "Dead tap",
                            "severity": "P1",
                            "evidence_tier": "OBSERVED",
                        },
                        {
                            "id": "F-002",
                            "title": "Simulated gripe",
                            "severity": "P2",
                            "evidence_tier": "SIMULATED",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        return output_dir

    def test_triage_persists_and_reports_precision(self, tmp_path, monkeypatch, capsys):
        config_path = _valid_config_yaml(tmp_path)
        output_dir = self._seed_round(tmp_path)

        answers = iter(["a", "r", "n"])  # gate=approve, F-001=real, F-002=noise
        monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

        rc = main(["triage", "--config", str(config_path)])

        assert rc == 0
        record = json.loads(
            (output_dir / "rounds.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        assert record["gate"] == "approve"
        assert record["triage"] == [
            {"id": "F-001", "verdict": "real"},
            {"id": "F-002", "verdict": "noise"},
        ]
        out = capsys.readouterr().out
        assert "precision at gate: 1/2 graded real" in out
        assert "observed tier 1/1" in out
        assert "simulated tier 0/1" in out

    def test_triage_without_runs_fails_with_guidance(self, tmp_path, capsys):
        config_path = _valid_config_yaml(tmp_path)
        (tmp_path / "output").mkdir(exist_ok=True)

        rc = main(["triage", "--config", str(config_path)])

        assert rc == 1
        assert "no runs recorded" in capsys.readouterr().err

    def test_triage_warns_on_stale_findings(self, tmp_path, monkeypatch, capsys):
        config_path = _valid_config_yaml(tmp_path)
        output_dir = self._seed_round(tmp_path)
        # findings stamped with a DIFFERENT run than the ledger's latest
        findings = json.loads(
            (output_dir / "findings.json").read_text(encoding="utf-8")
        )
        findings["run_id"] = "r-20260101-000000"
        (output_dir / "findings.json").write_text(
            json.dumps(findings), encoding="utf-8"
        )

        answers = iter(["a", "r", "n"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

        rc = main(["triage", "--config", str(config_path)])

        assert rc == 0
        assert "may be stale" in capsys.readouterr().err


class TestTriageHarnessWarning:
    """A finding is only interpretable against the build that produced it.

    The incident: a round reported a false "control does nothing" because it
    ran on a build predating the click-discipline prompt fix. Nothing in the
    run's records said so -- it took a manual grep of the installed wrapper.
    This warning is that grep, automated.
    """

    def _seed(self, tmp_path: Path, harness: dict | None) -> Path:
        """Seed a ledger record (optionally carrying harness provenance)."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(exist_ok=True)
        record: dict = {
            "run_id": "r-20260725-120000",
            "status": "gate_reached",
            "gate_reached": True,
            "gate": None,
            "triage": None,
        }
        if harness is not None:
            record["harness"] = harness
        (output_dir / "rounds.jsonl").write_text(
            json.dumps(record) + "\n", encoding="utf-8"
        )
        (output_dir / "findings.json").write_text(
            json.dumps(
                {
                    "run_id": "r-20260725-120000",
                    "findings": [
                        {
                            "id": "F-001",
                            "title": "Control does nothing",
                            "severity": "P1",
                            "evidence_tier": "OBSERVED",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return output_dir

    def _current_harness(self, config_path: Path) -> dict:
        """Provenance as triage will compute it for this config."""
        from amplifier_simulated_user_research.config import RoundConfig
        from amplifier_simulated_user_research.provenance import harness_provenance

        return harness_provenance(
            RoundConfig.from_yaml(config_path), include_engine=False
        )

    def _answer(self, monkeypatch, answers: list[str]) -> None:
        queue = iter(answers)
        monkeypatch.setattr("builtins.input", lambda prompt="": next(queue))

    def test_warns_when_harness_differs(self, tmp_path, monkeypatch, capsys):
        config_path = _valid_config_yaml(tmp_path)
        self._seed(
            tmp_path,
            {"tool_version": "0.0.1-ancient", "wrapper_sha256": "beforefix123"},
        )
        self._answer(monkeypatch, ["a", "r"])

        rc = main(["triage", "--config", str(config_path)])

        assert rc == 0
        err = capsys.readouterr().err
        assert "harness mismatch" in err
        assert "beforefix123" in err  # what the run used
        assert "0.0.1-ancient" in err
        assert "Re-run the round" in err  # actionable

    def test_silent_when_harness_matches(self, tmp_path, monkeypatch, capsys):
        config_path = _valid_config_yaml(tmp_path)
        self._seed(tmp_path, self._current_harness(config_path))
        self._answer(monkeypatch, ["a", "r"])

        rc = main(["triage", "--config", str(config_path)])

        assert rc == 0
        err = capsys.readouterr().err
        assert "harness mismatch" not in err
        assert "predates" not in err

    def test_engine_path_difference_alone_does_not_warn(
        self, tmp_path, monkeypatch, capsys
    ):
        """Engine location varies by machine -- warning on it would be noise."""
        config_path = _valid_config_yaml(tmp_path)
        harness = self._current_harness(config_path)
        harness["engine_path"] = "/some/other/machine/bin/attractor"
        harness["engine_source"] = "PATH"
        self._seed(tmp_path, harness)
        self._answer(monkeypatch, ["a", "r"])

        rc = main(["triage", "--config", str(config_path)])

        assert rc == 0
        assert "harness mismatch" not in capsys.readouterr().err

    def test_old_record_without_harness_is_tolerated(
        self, tmp_path, monkeypatch, capsys
    ):
        """Append-only ledger: pre-feature records simply lack the key. The
        reader must say so plainly and still complete the triage."""
        config_path = _valid_config_yaml(tmp_path)
        output_dir = self._seed(tmp_path, None)
        self._answer(monkeypatch, ["a", "r"])

        rc = main(["triage", "--config", str(config_path)])

        assert rc == 0
        captured = capsys.readouterr()
        assert "predates" in captured.err
        assert "harness mismatch" not in captured.err
        # ...and the verdicts still persisted
        record = json.loads(
            (output_dir / "rounds.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        assert record["gate"] == "approve"
        assert record["triage"] == [{"id": "F-001", "verdict": "real"}]
