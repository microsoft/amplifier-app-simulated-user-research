"""Tests for run_round(): command building, artifact inspection, gate detection.

subprocess.run is mocked throughout -- these tests never invoke the real
`attractor` CLI (that's the L4 proof, run separately against a live setup).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from amplifier_simulated_user_research.config import RoundConfig
from amplifier_simulated_user_research.runner import (
    _parse_attractor_status,
    resolve_attractor_command,
    run_round,
)


def _config(tmp_path: Path, **overrides: Any) -> RoundConfig:
    personas_dir = tmp_path / "personas"
    personas_dir.mkdir(exist_ok=True)
    kwargs: dict[str, Any] = dict(
        target_url="http://127.0.0.1:8892",
        seed_command="true",
        seed_cwd=str(tmp_path),
        personas_dir=str(personas_dir),
        output_dir=str(tmp_path / "output"),
        app_source_hint=str(tmp_path),
        personas=["marisol", "dev", "ken"],
        api_key="test-key",
        sur_repo_dir=str(tmp_path),  # avoid touching the real repo in unit tests
    )
    kwargs.update(overrides)
    return RoundConfig(**kwargs)


class TestResolveAttractorCommand:
    def test_missing_checkout_raises(self, tmp_path):
        with pytest.raises(
            RuntimeError, match="does not contain modules/pipeline-runner"
        ):
            resolve_attractor_command(str(tmp_path / "nonexistent-checkout"))

    def test_checkout_shells_via_uv_run(self, tmp_path):
        checkout = tmp_path / "attractor-checkout"
        (checkout / "modules" / "pipeline-runner").mkdir(parents=True)
        cmd = resolve_attractor_command(str(checkout))
        assert cmd[:2] == ["uv", "run"]
        assert cmd[-1] == "attractor"

    def test_raises_when_not_found_anywhere(self, monkeypatch):
        monkeypatch.setattr(
            "amplifier_simulated_user_research.runner.shutil.which", lambda name: None
        )
        monkeypatch.setattr(
            "amplifier_simulated_user_research.runner.Path.is_file", lambda self: False
        )
        with pytest.raises(RuntimeError, match="attractor console script not found"):
            resolve_attractor_command(None)

    def test_finds_via_which(self, monkeypatch):
        monkeypatch.setattr(
            "amplifier_simulated_user_research.runner.shutil.which",
            lambda name: "/usr/bin/attractor",
        )
        assert resolve_attractor_command(None) == ["/usr/bin/attractor"]


class TestParseAttractorStatus:
    def test_parses_status_line(self):
        stdout = "attractor: running pipeline\nattractor: status=success\nattractor: logs=/tmp\n"
        assert _parse_attractor_status(stdout) == "success"

    def test_falls_back_to_json_line(self):
        stdout = (
            'attractor: running\n{"status": "fail", "notes": "x", "logs_dir": "/tmp"}\n'
        )
        assert _parse_attractor_status(stdout) == "fail"

    def test_returns_none_when_unparseable(self):
        assert _parse_attractor_status("no useful output here") is None


class TestRunRound:
    def test_invalid_config_raises_value_error(self, tmp_path):
        config = _config(tmp_path, personas=["only-two", "personas"])
        with pytest.raises(ValueError, match="invalid RoundConfig"):
            run_round(config)

    def test_completed_status_on_clean_exit(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        output_dir = Path(config.output_dir)
        output_dir.mkdir(parents=True)
        (output_dir / "research-spec.md").write_text("x" * 2000, encoding="utf-8")

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0
        mock_result.stdout = "attractor: status=success\n"
        mock_result.stderr = ""
        monkeypatch.setattr(
            "amplifier_simulated_user_research.runner.subprocess.run",
            lambda *a, **k: mock_result,
        )
        monkeypatch.setattr(
            "amplifier_simulated_user_research.runner.resolve_attractor_command",
            lambda checkout: ["attractor"],
        )

        result = run_round(config, on_human_gate="auto-approve")
        assert result.status == "completed"
        assert result.exit_code == 0
        assert not result.gate_reached
        assert "research-spec.md" in result.artifacts

    def test_gate_reached_when_spec_exists_and_nonzero_exit(
        self, tmp_path, monkeypatch
    ):
        config = _config(tmp_path)
        output_dir = Path(config.output_dir)
        output_dir.mkdir(parents=True)
        (output_dir / "research-spec.md").write_text("x" * 2000, encoding="utf-8")
        (output_dir / "capture-notes.md").write_text("x" * 900, encoding="utf-8")

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 1
        mock_result.stdout = "attractor: status=fail\n"
        mock_result.stderr = "human gate pending"
        monkeypatch.setattr(
            "amplifier_simulated_user_research.runner.subprocess.run",
            lambda *a, **k: mock_result,
        )
        monkeypatch.setattr(
            "amplifier_simulated_user_research.runner.resolve_attractor_command",
            lambda checkout: ["attractor"],
        )

        result = run_round(config, on_human_gate="fail")
        assert result.status == "gate_reached"
        assert result.gate_reached is True
        assert result.artifacts["research-spec.md"] == output_dir / "research-spec.md"
        assert result.artifacts["capture-notes.md"] == output_dir / "capture-notes.md"

    def test_failed_status_when_spec_missing(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        Path(config.output_dir).mkdir(parents=True)

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 1
        mock_result.stdout = "attractor: status=fail\n"
        mock_result.stderr = "boom"
        monkeypatch.setattr(
            "amplifier_simulated_user_research.runner.subprocess.run",
            lambda *a, **k: mock_result,
        )
        monkeypatch.setattr(
            "amplifier_simulated_user_research.runner.resolve_attractor_command",
            lambda checkout: ["attractor"],
        )

        result = run_round(config, on_human_gate="fail")
        assert result.status == "failed"
        assert not result.gate_reached
        assert "research-spec.md" not in result.artifacts

    def test_command_includes_expected_flags(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        Path(config.output_dir).mkdir(parents=True)

        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            mock_result = MagicMock(spec=subprocess.CompletedProcess)
            mock_result.returncode = 0
            mock_result.stdout = "attractor: status=success\n"
            mock_result.stderr = ""
            return mock_result

        monkeypatch.setattr(
            "amplifier_simulated_user_research.runner.subprocess.run", fake_run
        )
        monkeypatch.setattr(
            "amplifier_simulated_user_research.runner.resolve_attractor_command",
            lambda checkout: ["attractor"],
        )

        run_round(config, on_human_gate="fail")
        command = captured["command"]
        assert command[0] == "attractor"
        assert command[1] == "run"
        assert "--provider" in command and "anthropic" in command
        assert "--on-human-gate" in command and "fail" in command
        assert "--cwd" in command
        # every to_dot_params() key should show up as a --param
        for key in config.to_dot_params():
            assert any(f"{key}=" in arg for arg in command if arg.startswith(f"{key}="))
