"""Tests for run_round(): command building, artifact inspection, gate detection,
run identity, and the rounds.jsonl ledger.

subprocess.run is mocked throughout -- these tests never invoke the real
`attractor` CLI (that's the L4 proof, run separately against a live setup).
"""

from __future__ import annotations

import json
import os
import re
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
    _mine_per_stage_wall_clock,
    _parse_attractor_status,
    generate_run_id,
    normalize_gate_policy,
    reset_attractor_resolution_cache,
    resolve_attractor_command,
    resolve_attractor_resolution,
    run_round,
    validate_attractor_binary,
)

# --- Fake engine binaries (the `attractor` name-collision incident) --------
#
# A real, unrelated package installs a binary ALSO named `attractor` at
# ~/.local/bin. These fakes reproduce both shapes so resolution order and
# identity validation are pinned by tests rather than by PATH luck.

_OURS_HELP = """\
usage: attractor run [-h] [--dot-source DOT_SOURCE] [--param k=v]
                     [--provider PROVIDER] [--logs-root LOGS_ROOT] [--cwd CWD]
                     [--on-human-gate {fail,auto-approve,console}]
"""

_FOREIGN_HELP = """\
usage: attractor [-h] [--serve] [--host HOST] [--port PORT] [--goal GOAL]
                 [--workdir WORKDIR] [--simulate] [--auto-approve]
                 [--validate-only] [--logs-dir LOGS_DIR] [dot_file]
"""


def _write_fake_attractor(
    bin_dir: Path, *, ours: bool = True, exit_code: int = 0
) -> Path:
    """Install a fake `attractor` executable into bin_dir; return its path.

    Written as a Python script with an absolute-interpreter shebang, NOT a
    /bin/sh script: several tests deliberately empty or replace PATH, and a
    shell fake would then fail to find even `cat` (an earlier version of
    this helper did exactly that and produced a misleading empty --help).
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe = bin_dir / "attractor"
    help_text = _OURS_HELP if ours else _FOREIGN_HELP
    exe.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"sys.stdout.write({help_text!r})\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return exe


def _point_interpreter_at(monkeypatch, bin_dir: Path) -> None:
    """Make runner treat bin_dir as the running interpreter's directory."""
    monkeypatch.setattr(
        "amplifier_simulated_user_research.runner.sys.executable",
        str(bin_dir / "python3"),
    )


@pytest.fixture(autouse=True)
def _clear_resolution_cache():
    """Resolution is cached per process -- isolate every test."""
    reset_attractor_resolution_cache()
    yield
    reset_attractor_resolution_cache()


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


def _mock_attractor(monkeypatch, *, returncode: int = 0, stdout: str | None = None):
    """Patch subprocess.run + attractor resolution; returns the capture dict."""
    captured: dict[str, Any] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = returncode
        mock_result.stdout = (
            stdout
            if stdout is not None
            else (
                "attractor: status=success\n"
                if returncode == 0
                else "attractor: status=fail\n"
            )
        )
        mock_result.stderr = "" if returncode == 0 else "boom"
        return mock_result

    monkeypatch.setattr(
        "amplifier_simulated_user_research.runner.subprocess.run", fake_run
    )
    monkeypatch.setattr(
        "amplifier_simulated_user_research.runner.resolve_attractor_command",
        lambda checkout: ["attractor"],
    )
    return captured


class TestValidateAttractorBinary:
    """Identity, not presence: does this binary speak our engine's CLI?"""

    def test_accepts_our_engine(self, tmp_path):
        exe = _write_fake_attractor(tmp_path / "bin", ours=True)
        ok, reason = validate_attractor_binary(exe)
        assert ok is True
        assert "advertises" in reason

    def test_rejects_foreign_binary_of_the_same_name(self, tmp_path):
        exe = _write_fake_attractor(tmp_path / "bin", ours=False)
        ok, reason = validate_attractor_binary(exe)
        assert ok is False
        # names the missing flags and says why
        assert "--param" in reason
        assert "different tool" in reason

    def test_rejects_nonzero_exit(self, tmp_path):
        exe = _write_fake_attractor(tmp_path / "bin", ours=True, exit_code=3)
        ok, reason = validate_attractor_binary(exe)
        assert ok is False
        assert "exited 3" in reason

    def test_rejects_unexecutable_path(self, tmp_path):
        ok, reason = validate_attractor_binary(tmp_path / "does-not-exist")
        assert ok is False
        assert "could not execute" in reason


class TestAttractorResolutionOrder:
    def test_interpreter_sibling_preferred_over_path(self, tmp_path, monkeypatch):
        """THE REGRESSION: PATH order must not decide which engine we run."""
        sibling_bin = tmp_path / "venv-bin"
        path_bin = tmp_path / "path-bin"
        sibling_exe = _write_fake_attractor(sibling_bin, ours=True)
        _write_fake_attractor(path_bin, ours=True)  # also valid, but on PATH
        _point_interpreter_at(monkeypatch, sibling_bin)
        monkeypatch.setenv("PATH", str(path_bin))

        resolution = resolve_attractor_resolution(None)

        assert resolution.command == [str(sibling_exe)]
        assert resolution.source == "interpreter-sibling"

    def test_foreign_binary_first_on_path_is_skipped_for_sibling(
        self, tmp_path, monkeypatch
    ):
        """The exact live incident: foreign `attractor` first on PATH."""
        sibling_bin = tmp_path / "venv-bin"
        foreign_bin = tmp_path / "usr-local-bin"
        sibling_exe = _write_fake_attractor(sibling_bin, ours=True)
        _write_fake_attractor(foreign_bin, ours=False)
        _point_interpreter_at(monkeypatch, sibling_bin)
        monkeypatch.setenv("PATH", str(foreign_bin))

        assert resolve_attractor_command(None) == [str(sibling_exe)]

    def test_foreign_on_path_rejected_and_next_path_candidate_used(
        self, tmp_path, monkeypatch
    ):
        """No valid sibling: walk PATH in order, rejecting foreign binaries."""
        empty_sibling = tmp_path / "no-engine-here"
        empty_sibling.mkdir()
        foreign_bin = tmp_path / "foreign-bin"
        real_bin = tmp_path / "real-bin"
        _write_fake_attractor(foreign_bin, ours=False)
        real_exe = _write_fake_attractor(real_bin, ours=True)
        _point_interpreter_at(monkeypatch, empty_sibling)
        monkeypatch.setenv("PATH", f"{foreign_bin}{os.pathsep}{real_bin}")

        resolution = resolve_attractor_resolution(None)

        assert resolution.command == [str(real_exe)]
        assert resolution.source == "PATH"
        assert len(resolution.rejected) == 1
        rejected_path, reason = resolution.rejected[0]
        assert rejected_path == str(foreign_bin / "attractor")
        assert "--param" in reason

    def test_all_candidates_foreign_fails_loud(self, tmp_path, monkeypatch):
        empty_sibling = tmp_path / "no-engine-here"
        empty_sibling.mkdir()
        foreign_bin = tmp_path / "foreign-bin"
        _write_fake_attractor(foreign_bin, ours=False)
        _point_interpreter_at(monkeypatch, empty_sibling)
        monkeypatch.setenv("PATH", str(foreign_bin))

        with pytest.raises(RuntimeError) as excinfo:
            resolve_attractor_command(None)

        msg = str(excinfo.value)
        assert "no usable attractor engine found" in msg
        assert str(foreign_bin / "attractor") in msg  # what was found
        assert "--param" in msg  # why it was rejected
        assert "amplifier-module-pipeline-runner" in msg  # the remedy
        assert "foreign binary is rejected" in msg  # the likely cause

    def test_no_candidates_at_all_fails_loud(self, tmp_path, monkeypatch):
        empty_sibling = tmp_path / "no-engine-here"
        empty_sibling.mkdir()
        _point_interpreter_at(monkeypatch, empty_sibling)
        monkeypatch.setenv("PATH", str(tmp_path / "empty-path-dir"))

        with pytest.raises(RuntimeError) as excinfo:
            resolve_attractor_command(None)

        msg = str(excinfo.value)
        assert "not found next to the current Python interpreter" in msg
        assert "amplifier-module-pipeline-runner" in msg

    def test_resolution_is_cached_per_process(self, tmp_path, monkeypatch):
        sibling_bin = tmp_path / "venv-bin"
        _write_fake_attractor(sibling_bin, ours=True)
        _point_interpreter_at(monkeypatch, sibling_bin)
        monkeypatch.setenv("PATH", "")

        probes: list[str] = []
        real_validate = validate_attractor_binary

        def counting_validate(binary):
            probes.append(str(binary))
            return real_validate(binary)

        monkeypatch.setattr(
            "amplifier_simulated_user_research.runner.validate_attractor_binary",
            counting_validate,
        )

        first = resolve_attractor_command(None)
        second = resolve_attractor_command(None)

        assert first == second
        assert len(probes) == 1  # second call served from cache


class TestAttractorCheckoutOverride:
    def test_checkout_takes_precedence_over_everything(self, tmp_path, monkeypatch):
        """Explicit operator override wins -- and needs no identity probe."""
        sibling_bin = tmp_path / "venv-bin"
        _write_fake_attractor(sibling_bin, ours=True)
        _point_interpreter_at(monkeypatch, sibling_bin)
        checkout = tmp_path / "attractor-checkout"
        (checkout / "modules" / "pipeline-runner").mkdir(parents=True)

        def exploding_validate(binary):  # must never be called
            raise AssertionError("explicit checkout must not be identity-probed")

        monkeypatch.setattr(
            "amplifier_simulated_user_research.runner.validate_attractor_binary",
            exploding_validate,
        )

        resolution = resolve_attractor_resolution(str(checkout))

        assert resolution.source == "checkout"
        assert resolution.command[:2] == ["uv", "run"]
        assert resolution.command[-1] == "attractor"

    def test_missing_checkout_raises(self, tmp_path):
        with pytest.raises(
            RuntimeError, match="does not contain modules/pipeline-runner"
        ):
            resolve_attractor_command(str(tmp_path / "nonexistent-checkout"))


class TestRunIdentity:
    def test_generate_run_id_format(self):
        run_id = generate_run_id()
        assert re.match(r"^r-\d{8}-\d{6}$", run_id)
        assert RUN_ID_PATTERN.match(run_id)

    def test_generate_run_id_deterministic_for_fixed_time(self):
        import datetime as dt

        fixed = dt.datetime(2026, 7, 23, 14, 5, 9)
        assert generate_run_id(fixed) == "r-20260723-140509"

    def test_run_round_rejects_malformed_run_id(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        _mock_attractor(monkeypatch)
        with pytest.raises(ValueError, match="does not match the required format"):
            run_round(config, run_id="not-a-run-id")

    def test_run_id_passed_as_param_and_carried_in_result(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        captured = _mock_attractor(monkeypatch)

        result = run_round(config, run_id="r-20260723-120000")

        assert result.run_id == "r-20260723-120000"
        assert "run_id=r-20260723-120000" in captured["command"]


class TestConsoleGateMode:
    def test_console_maps_to_engine_console(self):
        assert normalize_gate_policy("console") == "console"

    def test_cli_parser_accepts_console_choice(self):
        from amplifier_simulated_user_research.cli import build_parser

        args = build_parser().parse_args(
            ["run", "--config", "project.yaml", "--on-human-gate", "console"]
        )
        assert args.on_human_gate == "console"

    def _console_run(self, tmp_path, monkeypatch, *, returncode: int = 0):
        """Run run_round in console mode; capture the subprocess kwargs."""
        config = _config(tmp_path)
        captured: dict[str, Any] = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            # stdout/stderr None: inherited streams -- any accidental
            # proc.stdout[-4000:] access would raise TypeError.
            return subprocess.CompletedProcess(
                command, returncode=returncode, stdout=None, stderr=None
            )

        monkeypatch.setattr(
            "amplifier_simulated_user_research.runner.subprocess.run", fake_run
        )
        monkeypatch.setattr(
            "amplifier_simulated_user_research.runner.resolve_attractor_command",
            lambda checkout: ["attractor"],
        )
        result = run_round(config, on_human_gate="console", run_id="r-20260724-020000")
        return config, captured, result

    def test_console_mode_inherits_stdio(self, tmp_path, monkeypatch):
        """CRITICAL PLUMBING: the engine's ConsoleInterviewer must talk to the
        human -- stdin/stdout/stderr are inherited, never captured/piped."""
        _, captured, _ = self._console_run(tmp_path, monkeypatch)

        kwargs = captured["kwargs"]
        assert "capture_output" not in kwargs
        assert (
            "stdin" not in kwargs and "stdout" not in kwargs and "stderr" not in kwargs
        )
        idx = captured["command"].index("--on-human-gate")
        assert captured["command"][idx + 1] == "console"

    def test_console_mode_ledger_from_ground_truth(self, tmp_path, monkeypatch):
        """No parsed output in console mode -- the ledger record is still
        written, derived from ground truth (exit code, artifacts on disk)."""
        config, _, result = self._console_run(tmp_path, monkeypatch)

        assert result.status == "completed"
        assert result.attractor_status is None  # nothing captured to parse
        assert result.stdout_tail == "" and result.stderr_tail == ""

        record = json.loads(
            (Path(config.output_dir) / "rounds.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
        assert record["run_id"] == "r-20260724-020000"
        assert record["status"] == "completed"
        assert record["gate"] is None and record["triage"] is None

    def test_console_rejected_by_old_engine_fails_loud(self, tmp_path, monkeypatch):
        """argparse exit 2 from an engine without console-gate support must
        surface the exact situation + both fixes, not a raw traceback."""
        with pytest.raises(RuntimeError) as excinfo:
            self._console_run(tmp_path, monkeypatch, returncode=2)

        msg = str(excinfo.value)
        assert "console" in msg and "exit 2" in msg
        assert "attractor_checkout" in msg  # fix 1: local checkout with the feature
        assert "upstream merge" in msg  # fix 2: wait for @main
        # the invocation never ran a round: no ledger record
        assert not (tmp_path / "output" / "rounds.jsonl").exists()

    def test_stop_mode_still_captures_output(self, tmp_path, monkeypatch):
        """The inherit-stdio trade applies ONLY to console mode."""
        config = _config(tmp_path)
        captured: dict[str, Any] = {}

        def fake_run(command, **kwargs):
            captured["kwargs"] = kwargs
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

        result = run_round(config, on_human_gate="stop")
        assert captured["kwargs"].get("capture_output") is True
        assert result.attractor_status == "success"


class TestGatePolicy:
    def test_stop_maps_to_engine_fail(self):
        assert normalize_gate_policy("stop") == "fail"

    def test_fail_is_deprecated_alias_for_stop(self):
        assert normalize_gate_policy("fail") == "fail"

    def test_auto_approve_passes_through(self):
        assert normalize_gate_policy("auto-approve") == "auto-approve"

    def test_unknown_policy_raises(self):
        with pytest.raises(ValueError, match="unknown on_human_gate policy"):
            normalize_gate_policy("explode")

    def test_run_round_sends_engine_fail_for_stop(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        captured = _mock_attractor(monkeypatch)

        run_round(config, on_human_gate="stop")

        command = captured["command"]
        idx = command.index("--on-human-gate")
        assert command[idx + 1] == "fail"

    def test_run_round_rejects_unknown_policy(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        _mock_attractor(monkeypatch)
        with pytest.raises(ValueError, match="unknown on_human_gate policy"):
            run_round(config, on_human_gate="explode")


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


class TestPerStageWallClock:
    def test_mines_duration_ms_from_status_json(self, tmp_path):
        for node, ms in (("capture_screens", 197463.9), ("synthesis", 61000)):
            node_dir = tmp_path / node
            node_dir.mkdir()
            (node_dir / "status.json").write_text(
                json.dumps({"node_id": node, "duration_ms": ms}), encoding="utf-8"
            )
        # a node dir without status.json and one with junk -- both skipped
        (tmp_path / "gate").mkdir()
        bad = tmp_path / "check_seed"
        bad.mkdir()
        (bad / "status.json").write_text("not json", encoding="utf-8")

        stages = _mine_per_stage_wall_clock(tmp_path)

        assert stages == {"capture_screens": 197.464, "synthesis": 61.0}

    def test_returns_none_when_nothing_derivable(self, tmp_path):
        assert _mine_per_stage_wall_clock(tmp_path / "missing") is None
        empty = tmp_path / "empty-logs"
        empty.mkdir()
        assert _mine_per_stage_wall_clock(empty) is None


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

        _mock_attractor(monkeypatch, returncode=0)

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

        _mock_attractor(monkeypatch, returncode=1)

        result = run_round(config, on_human_gate="stop")
        assert result.status == "gate_reached"
        assert result.gate_reached is True
        assert result.artifacts["research-spec.md"] == output_dir / "research-spec.md"
        assert result.artifacts["capture-notes.md"] == output_dir / "capture-notes.md"

    def test_gate_reached_under_deprecated_fail_alias(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        output_dir = Path(config.output_dir)
        output_dir.mkdir(parents=True)
        (output_dir / "research-spec.md").write_text("x" * 2000, encoding="utf-8")

        _mock_attractor(monkeypatch, returncode=1)

        result = run_round(config, on_human_gate="fail")
        assert result.status == "gate_reached"
        assert result.gate_reached is True

    def test_failed_status_when_spec_missing(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        Path(config.output_dir).mkdir(parents=True)

        _mock_attractor(monkeypatch, returncode=1)

        result = run_round(config, on_human_gate="stop")
        assert result.status == "failed"
        assert not result.gate_reached
        assert "research-spec.md" not in result.artifacts

    def test_findings_json_collected_as_artifact(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        output_dir = Path(config.output_dir)
        output_dir.mkdir(parents=True)
        (output_dir / "findings.json").write_text(
            '{"run_id": "r-20260723-120000", "findings": []}', encoding="utf-8"
        )

        _mock_attractor(monkeypatch, returncode=0)

        result = run_round(config)
        assert "findings.json" in result.artifacts

    def test_command_includes_expected_flags(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        Path(config.output_dir).mkdir(parents=True)

        captured = _mock_attractor(monkeypatch, returncode=0)

        run_round(config, on_human_gate="stop")
        command = captured["command"]
        assert command[0] == "attractor"
        assert command[1] == "run"
        assert "--provider" in command and "anthropic" in command
        assert "--on-human-gate" in command and "fail" in command
        assert "--cwd" in command
        # every to_dot_params() key should show up as a --param
        for key in config.to_dot_params():
            assert any(arg.startswith(f"{key}=") for arg in command)
        # plus the run identity param
        assert any(arg.startswith("run_id=r-") for arg in command)

    def test_browser_env_exported_into_subprocess(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AGENT_BROWSER_EXECUTABLE_PATH", raising=False)
        monkeypatch.delenv("AGENT_BROWSER_ARGS", raising=False)
        config = _config(
            tmp_path,
            browser_executable_path="/opt/headless_shell",
            browser_args="--no-sandbox",
        )

        captured: dict[str, Any] = {}

        def fake_run(command, **kwargs):
            captured["env"] = kwargs.get("env")
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

        run_round(config)

        env = captured["env"]
        assert env is not None
        assert env["AGENT_BROWSER_EXECUTABLE_PATH"] == "/opt/headless_shell"
        assert env["AGENT_BROWSER_ARGS"] == "--no-sandbox"
        assert "PATH" in env  # os.environ preserved, not replaced

    def test_no_browser_env_keys_when_unconfigured(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AGENT_BROWSER_EXECUTABLE_PATH", raising=False)
        monkeypatch.delenv("AGENT_BROWSER_ARGS", raising=False)
        config = _config(tmp_path)

        captured: dict[str, Any] = {}

        def fake_run(command, **kwargs):
            captured["env"] = kwargs.get("env")
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

        run_round(config)

        env = captured["env"]
        assert "AGENT_BROWSER_EXECUTABLE_PATH" not in env
        assert "AGENT_BROWSER_ARGS" not in env

    def test_logs_dir_is_per_run_subdirectory(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        _mock_attractor(monkeypatch, returncode=0)

        result = run_round(config, run_id="r-20260723-120000")

        assert result.logs_dir is not None
        assert result.logs_dir.name == "r-20260723-120000"
        assert result.logs_dir.is_dir()


class TestRoundsLedger:
    def test_appends_one_record_per_run(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        output_dir = Path(config.output_dir)
        _mock_attractor(monkeypatch, returncode=0)

        result = run_round(config, run_id="r-20260723-120000")

        rounds_path = output_dir / "rounds.jsonl"
        assert result.rounds_path == rounds_path
        lines = rounds_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["run_id"] == "r-20260723-120000"
        assert record["status"] == "completed"
        assert record["gate_reached"] is False
        assert record["prior_run_id"] is None
        assert record["gate"] is None
        assert record["triage"] is None
        assert isinstance(record["wall_clock_s"], float)
        assert record["ts_start"] <= record["ts_end"]

    def test_second_run_links_to_prior_run_id(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        _mock_attractor(monkeypatch, returncode=0)

        run_round(config, run_id="r-20260723-120000")
        run_round(config, run_id="r-20260723-130000")

        lines = (
            (Path(config.output_dir) / "rounds.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        assert len(lines) == 2
        second = json.loads(lines[1])
        assert second["run_id"] == "r-20260723-130000"
        assert second["prior_run_id"] == "r-20260723-120000"

    def test_record_carries_artifact_names(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        output_dir = Path(config.output_dir)
        output_dir.mkdir(parents=True)
        (output_dir / "research-spec.md").write_text("x" * 2000, encoding="utf-8")
        (output_dir / "persona-marisol.md").write_text("x" * 2000, encoding="utf-8")
        _mock_attractor(monkeypatch, returncode=1)

        run_round(config, on_human_gate="stop", run_id="r-20260723-120000")

        record = json.loads(
            (output_dir / "rounds.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        assert record["status"] == "gate_reached"
        assert "research-spec.md" in record["artifacts"]
        assert "persona-marisol.md" in record["artifacts"]

    def test_per_stage_wall_clock_mined_into_record(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        output_dir = Path(config.output_dir)
        run_id = "r-20260723-120000"

        def fake_run(command, **kwargs):
            # simulate the engine writing a node status.json into this
            # run's logs dir during execution
            node_dir = output_dir / ".attractor-logs" / run_id / "synthesis"
            node_dir.mkdir(parents=True, exist_ok=True)
            (node_dir / "status.json").write_text(
                json.dumps({"duration_ms": 61000}), encoding="utf-8"
            )
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

        run_round(config, run_id=run_id)

        record = json.loads(
            (output_dir / "rounds.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        assert record["per_stage_wall_clock"] == {"synthesis": 61.0}

    def test_per_stage_null_when_not_derivable(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        _mock_attractor(monkeypatch, returncode=0)

        run_round(config, run_id="r-20260723-120000")

        record = json.loads(
            (Path(config.output_dir) / "rounds.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
        assert record["per_stage_wall_clock"] is None
