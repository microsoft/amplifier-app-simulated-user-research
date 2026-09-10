"""Tests for the dot-runner adapter: resolution, invocation, and ledger facts.

All subprocess calls are mocked or target temporary fake executables. These
unit tests never invoke a real engine or browser.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from amplifier_simulated_user_research.config import RoundConfig
from amplifier_simulated_user_research.runner import (
    RUN_ID_PATTERN,
    DotRunnerResolution,
    _mine_per_stage_wall_clock,
    _parse_engine_status,
    generate_run_id,
    normalize_gate_policy,
    reset_dot_runner_resolution_cache,
    resolve_dot_runner_command,
    resolve_dot_runner_resolution,
    run_round,
    validate_dot_runner_binary,
)

_OURS_HELP = """\
usage: dot-runner run [-h] [--param k=v] [--provider PROVIDER]
                      [--worker NAME] [--logs-root LOGS_ROOT] [--cwd CWD]
                      [--on-human-gate {fail,auto-approve,console}]
"""
_FOREIGN_HELP = "usage: something-else run [-h] [--param k=v]\n"


@pytest.fixture(autouse=True)
def _clear_resolution_cache():
    reset_dot_runner_resolution_cache()
    yield
    reset_dot_runner_resolution_cache()


def _write_fake_dot_runner(bin_dir: Path, *, ours: bool = True, exit_code: int = 0) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    executable = bin_dir / "dot-runner"
    help_text = _OURS_HELP if ours else _FOREIGN_HELP
    executable.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stdout.write({help_text!r})\nsys.exit({exit_code})\n",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return executable


def _point_interpreter_at(monkeypatch, bin_dir: Path) -> None:
    monkeypatch.setattr(
        "amplifier_simulated_user_research.runner.sys.executable", str(bin_dir / "python3")
    )


def _config(tmp_path: Path, **overrides: Any) -> RoundConfig:
    personas_dir = tmp_path / "personas"
    personas_dir.mkdir(exist_ok=True)
    kwargs: dict[str, Any] = {
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
    kwargs.update(overrides)
    return RoundConfig(**kwargs)


def _stub_engine_resolution(monkeypatch) -> None:
    monkeypatch.setattr(
        "amplifier_simulated_user_research.runner.resolve_dot_runner_resolution",
        lambda checkout: DotRunnerResolution(command=["dot-runner"], source="interpreter-sibling"),
    )


def _mock_dot_runner(monkeypatch, *, returncode: int = 0, stdout: str | None = None):
    captured: dict[str, Any] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        result = MagicMock(spec=subprocess.CompletedProcess)
        result.returncode = returncode
        result.stdout = stdout if stdout is not None else f"dot-runner: status={'success' if returncode == 0 else 'fail'}\n"
        result.stderr = "" if returncode == 0 else "boom"
        return result

    monkeypatch.setattr("amplifier_simulated_user_research.runner.subprocess.run", fake_run)
    monkeypatch.setattr(
        "amplifier_simulated_user_research.runner.resolve_dot_runner_command",
        lambda checkout: ["dot-runner"],
    )
    _stub_engine_resolution(monkeypatch)
    return captured


class TestDotRunnerResolution:
    def test_accepts_binary_with_all_required_run_flags(self, tmp_path):
        ok, reason = validate_dot_runner_binary(_write_fake_dot_runner(tmp_path / "bin"))
        assert ok is True
        assert "advertises" in reason

    def test_rejects_binary_missing_worker_flag(self, tmp_path):
        ok, reason = validate_dot_runner_binary(
            _write_fake_dot_runner(tmp_path / "bin", ours=False)
        )
        assert ok is False
        assert "--worker" in reason

    def test_prefers_interpreter_sibling_over_path(self, tmp_path, monkeypatch):
        sibling = _write_fake_dot_runner(tmp_path / "venv-bin")
        _write_fake_dot_runner(tmp_path / "path-bin")
        _point_interpreter_at(monkeypatch, sibling.parent)
        monkeypatch.setenv("PATH", str(tmp_path / "path-bin"))

        resolution = resolve_dot_runner_resolution()

        assert resolution.command == [str(sibling)]
        assert resolution.source == "interpreter-sibling"

    def test_rejects_path_candidate_before_selecting_valid_one(self, tmp_path, monkeypatch):
        foreign_dir = tmp_path / "foreign"
        valid = _write_fake_dot_runner(tmp_path / "valid")
        _write_fake_dot_runner(foreign_dir, ours=False)
        _point_interpreter_at(monkeypatch, tmp_path / "no-sibling")
        monkeypatch.setenv("PATH", f"{foreign_dir}{os.pathsep}{valid.parent}")

        resolution = resolve_dot_runner_resolution()

        assert resolution.command == [str(valid)]
        assert resolution.rejected[0][0] == str(foreign_dir / "dot-runner")

    def test_command_accessor_does_not_mutate_the_cached_prefix(self, tmp_path, monkeypatch):
        sibling = _write_fake_dot_runner(tmp_path / "venv-bin")
        _point_interpreter_at(monkeypatch, sibling.parent)
        monkeypatch.setenv("PATH", "")

        first = resolve_dot_runner_command()
        first.extend(["run", "pipeline.dot"])

        assert resolve_dot_runner_command() == [str(sibling)]

    def test_checkout_override_runs_dot_runner(self, tmp_path, monkeypatch):
        checkout = tmp_path / "dot-runner-checkout"
        runner_dir = checkout / "modules" / "pipeline-runner"
        runner_dir.mkdir(parents=True)
        (runner_dir / "pyproject.toml").write_text(
            (
                '[project]\nname = "amplifier-module-pipeline-runner"\n'
                '[project.scripts]\n'
                'dot-runner = "amplifier_module_pipeline_runner.cli:main"\n'
                '[tool.uv]\npackage = true\n'
            ),
            encoding="utf-8",
        )
        (runner_dir / "amplifier_module_pipeline_runner").mkdir()
        (runner_dir / "amplifier_module_pipeline_runner" / "cli.py").touch()
        monkeypatch.setattr(
            "amplifier_simulated_user_research.runner.validate_dot_runner_command",
            lambda command: (True, "advertises the engine's run flags"),
        )

        resolution = resolve_dot_runner_resolution(str(checkout))

        assert resolution.source == "checkout"
        assert resolution.command == [
            "uv",
            "run",
            "--project",
            str(runner_dir),
            "dot-runner",
        ]

    def test_checkout_override_rejects_wrong_project_metadata(self, tmp_path):
        checkout = tmp_path / "dot-runner-checkout"
        runner_dir = checkout / "modules" / "pipeline-runner"
        runner_dir.mkdir(parents=True)
        (runner_dir / "pyproject.toml").write_text(
            (
                '[project]\nname = "some-other-package"\n'
                '[project.scripts]\n'
                'dot-runner = "amplifier_module_pipeline_runner.cli:main"\n'
            ),
            encoding="utf-8",
        )

        with pytest.raises(RuntimeError, match="expected 'amplifier-module-pipeline-runner'"):
            resolve_dot_runner_resolution(str(checkout))

    def test_checkout_override_rejects_missing_dot_runner_entry_point(self, tmp_path):
        checkout = tmp_path / "dot-runner-checkout"
        runner_dir = checkout / "modules" / "pipeline-runner"
        runner_dir.mkdir(parents=True)
        (runner_dir / "pyproject.toml").write_text(
            '[project]\nname = "amplifier-module-pipeline-runner"\n',
            encoding="utf-8",
        )

        with pytest.raises(RuntimeError, match="invalid pipeline-runner dot-runner entry point"):
            resolve_dot_runner_resolution(str(checkout))

    def test_checkout_override_rejects_noninstallable_package(self, tmp_path):
        checkout = tmp_path / "dot-runner-checkout"
        runner_dir = checkout / "modules" / "pipeline-runner"
        runner_dir.mkdir(parents=True)
        (runner_dir / "pyproject.toml").write_text(
            (
                '[project]\nname = "amplifier-module-pipeline-runner"\n'
                '[project.scripts]\n'
                'dot-runner = "amplifier_module_pipeline_runner.cli:main"\n'
                '[tool.uv]\npackage = false\n'
            ),
            encoding="utf-8",
        )

        with pytest.raises(RuntimeError, match="not an installable pipeline-runner package"):
            resolve_dot_runner_resolution(str(checkout))

    def test_rejects_a_binary_that_exits_nonzero(self, tmp_path):
        ok, reason = validate_dot_runner_binary(
            _write_fake_dot_runner(tmp_path / "bin", exit_code=3)
        )

        assert ok is False
        assert "exited 3" in reason

    def test_rejects_a_nonexistent_binary(self, tmp_path):
        ok, reason = validate_dot_runner_binary(tmp_path / "missing")

        assert ok is False
        assert "could not execute" in reason

    def test_fails_loudly_when_no_candidate_is_usable(self, tmp_path, monkeypatch):
        _write_fake_dot_runner(tmp_path / "foreign", ours=False)
        _point_interpreter_at(monkeypatch, tmp_path / "no-sibling")
        monkeypatch.setenv("PATH", str(tmp_path / "foreign"))

        with pytest.raises(RuntimeError, match="no usable dot-runner engine found"):
            resolve_dot_runner_command()

    def test_fails_loudly_when_no_candidate_exists(self, tmp_path, monkeypatch):
        _point_interpreter_at(monkeypatch, tmp_path / "no-sibling")
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))

        with pytest.raises(RuntimeError, match="not found next to the current Python"):
            resolve_dot_runner_command()

    def test_resolution_probes_once_then_uses_the_cache(self, tmp_path, monkeypatch):
        sibling = _write_fake_dot_runner(tmp_path / "venv-bin")
        _point_interpreter_at(monkeypatch, sibling.parent)
        monkeypatch.setenv("PATH", "")
        probes: list[str] = []
        real_validate = validate_dot_runner_binary

        def counting_validate(binary):
            probes.append(str(binary))
            return real_validate(binary)

        monkeypatch.setattr(
            "amplifier_simulated_user_research.runner.validate_dot_runner_binary",
            counting_validate,
        )

        assert resolve_dot_runner_command() == [str(sibling)]
        assert resolve_dot_runner_command() == [str(sibling)]
        assert probes == [str(sibling)]

    def test_missing_checkout_raises_a_targeted_error(self, tmp_path):
        with pytest.raises(RuntimeError, match="does not contain modules/pipeline-runner"):
            resolve_dot_runner_command(str(tmp_path / "missing-checkout"))


class TestRunRound:
    def test_generates_valid_run_id(self):
        assert RUN_ID_PATTERN.match(generate_run_id())

    def test_command_selects_coding_agent_and_dot_runner_logs(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        captured = _mock_dot_runner(monkeypatch)

        result = run_round(config, run_id="r-20260910-120000")

        command = captured["command"]
        assert command[:2] == ["dot-runner", "run"]
        worker_index = command.index("--worker")
        assert command[worker_index + 1] == "coding-agent"
        assert result.logs_dir == Path(config.output_dir) / ".dot-runner-logs" / result.run_id
        assert result.engine_status == "success"
        assert result.attractor_status == result.engine_status

    def test_parses_dot_runner_status_and_json_fallback(self):
        assert _parse_engine_status("dot-runner: status=success\n") == "success"
        assert _parse_engine_status('{"status": "fail"}\n') == "fail"

    def test_gate_reached_when_spec_exists_after_stop(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        output_dir = Path(config.output_dir)
        output_dir.mkdir()
        (output_dir / "research-spec.md").write_text("ready", encoding="utf-8")
        _mock_dot_runner(monkeypatch, returncode=1)

        result = run_round(config, on_human_gate="stop", run_id="r-20260910-120000")

        assert result.status == "gate_reached"
        assert result.gate_reached is True

    def test_console_mode_inherits_stdio(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        captured: dict[str, Any] = {}

        def fake_run(command, **kwargs):
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(command, returncode=0, stdout=None, stderr=None)

        monkeypatch.setattr("amplifier_simulated_user_research.runner.subprocess.run", fake_run)
        monkeypatch.setattr(
            "amplifier_simulated_user_research.runner.resolve_dot_runner_command",
            lambda checkout: ["dot-runner"],
        )
        _stub_engine_resolution(monkeypatch)

        result = run_round(config, on_human_gate="console", run_id="r-20260910-120000")

        assert "capture_output" not in captured["kwargs"]
        assert result.engine_status is None

    def test_mines_engine_status_json(self, tmp_path):
        node_dir = tmp_path / "synthesis"
        node_dir.mkdir()
        (node_dir / "status.json").write_text(json.dumps({"duration_ms": 61000}), encoding="utf-8")
        assert _mine_per_stage_wall_clock(tmp_path) == {"synthesis": 61.0}

    def test_gate_policy_remains_compatible(self):
        assert normalize_gate_policy("stop") == "fail"
        assert normalize_gate_policy("console") == "console"

    def test_rejects_an_invalid_config_before_invoking_the_engine(self, tmp_path):
        with pytest.raises(ValueError, match="invalid RoundConfig"):
            run_round(_config(tmp_path, personas=["one", "two"]))

    def test_rejects_a_malformed_run_id(self, tmp_path, monkeypatch):
        _mock_dot_runner(monkeypatch)

        with pytest.raises(ValueError, match="does not match the required format"):
            run_round(_config(tmp_path), run_id="not-a-run-id")

    def test_explicit_run_id_is_forwarded_to_the_engine(self, tmp_path, monkeypatch):
        captured = _mock_dot_runner(monkeypatch)

        result = run_round(_config(tmp_path), run_id="r-20260910-120000")

        assert result.run_id == "r-20260910-120000"
        assert "run_id=r-20260910-120000" in captured["command"]

    def test_noninteractive_runs_capture_engine_output(self, tmp_path, monkeypatch):
        captured = _mock_dot_runner(monkeypatch)

        result = run_round(_config(tmp_path), run_id="r-20260910-120000")

        assert captured["kwargs"]["capture_output"] is True
        assert result.engine_status == "success"

    def test_deprecated_fail_gate_policy_still_reaches_the_gate(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        output_dir = Path(config.output_dir)
        output_dir.mkdir()
        (output_dir / "research-spec.md").write_text("ready", encoding="utf-8")
        _mock_dot_runner(monkeypatch, returncode=1)

        result = run_round(config, on_human_gate="fail", run_id="r-20260910-120000")

        assert result.status == "gate_reached"
        assert result.gate_reached is True

    def test_nonzero_exit_without_a_spec_is_a_failure(self, tmp_path, monkeypatch):
        _mock_dot_runner(monkeypatch, returncode=1)

        result = run_round(_config(tmp_path), run_id="r-20260910-120000")

        assert result.status == "failed"
        assert result.gate_reached is False

    def test_collects_each_existing_contract_artifact(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        output_dir = Path(config.output_dir)
        output_dir.mkdir()
        for name in (
            "capture-notes.md",
            "review-ia.md",
            "review-responsive.md",
            "persona-marisol.md",
            "persona-dev.md",
            "persona-ken.md",
            "research-spec.md",
            "findings.json",
        ):
            (output_dir / name).write_text("ready", encoding="utf-8")
        _mock_dot_runner(monkeypatch)

        result = run_round(config, run_id="r-20260910-120000")

        assert set(result.artifacts) == {
            "capture-notes.md",
            "review-ia.md",
            "review-responsive.md",
            "persona-marisol.md",
            "persona-dev.md",
            "persona-ken.md",
            "research-spec.md",
            "findings.json",
        }

    def test_passes_configured_browser_environment_to_the_engine(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AGENT_BROWSER_EXECUTABLE_PATH", raising=False)
        monkeypatch.delenv("AGENT_BROWSER_ARGS", raising=False)
        captured = _mock_dot_runner(monkeypatch)

        run_round(
            _config(
                tmp_path,
                browser_executable_path="/opt/headless_shell",
                browser_args="--no-sandbox",
            ),
            run_id="r-20260910-120000",
        )

        env = captured["kwargs"]["env"]
        assert env["AGENT_BROWSER_EXECUTABLE_PATH"] == "/opt/headless_shell"
        assert env["AGENT_BROWSER_ARGS"] == "--no-sandbox"

    def test_ledger_links_a_second_run_to_the_first(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        _mock_dot_runner(monkeypatch)

        run_round(config, run_id="r-20260910-120000")
        run_round(config, run_id="r-20260910-130000")

        records = [
            json.loads(line)
            for line in (Path(config.output_dir) / "rounds.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert [record["run_id"] for record in records] == [
            "r-20260910-120000",
            "r-20260910-130000",
        ]
        assert records[1]["prior_run_id"] == "r-20260910-120000"

    def test_ledger_persists_dot_runner_timings_and_harness(self, tmp_path, monkeypatch):
        config = _config(
            tmp_path,
            sur_repo_dir=str(Path(__file__).resolve().parents[1]),
        )
        logs_dir = Path(config.output_dir) / ".dot-runner-logs" / "r-20260910-120000"
        node_dir = logs_dir / "synthesis"
        node_dir.mkdir(parents=True)
        (node_dir / "status.json").write_text(
            json.dumps({"duration_ms": 61000}), encoding="utf-8"
        )
        _mock_dot_runner(monkeypatch)

        run_round(config, run_id="r-20260910-120000")

        record = json.loads(
            (Path(config.output_dir) / "rounds.jsonl").read_text(encoding="utf-8")
        )
        assert record["per_stage_wall_clock"] == {"synthesis": 61.0}
        assert record["harness"]["engine_source"] == "interpreter-sibling"
        assert record["harness"]["engine_path"] == "dot-runner"
        assert record["harness"]["wrapper_sha256"]
        assert record["harness"]["pipeline_sha256"]
