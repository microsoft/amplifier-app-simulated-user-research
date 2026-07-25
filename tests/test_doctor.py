"""Tests for doctor(): environment diagnostics, all filesystem/subprocess mocked."""

from __future__ import annotations

import importlib
import subprocess
from typing import Any

import pytest

from amplifier_simulated_user_research.config import RoundConfig
from amplifier_simulated_user_research.doctor import DoctorCheck, doctor

# NOTE: `import amplifier_simulated_user_research.doctor as doctor_mod` would
# bind the `doctor` FUNCTION (the package __init__ re-exports it, shadowing
# the submodule attribute) -- import the module object explicitly instead.
doctor_mod = importlib.import_module("amplifier_simulated_user_research.doctor")

# Captured BEFORE the autouse stub below patches the module attribute, so
# probe-behavior tests can exercise the real function with mocked subprocess.
_ORIG_PROBE = doctor_mod._probe_browser_launch


@pytest.fixture(autouse=True)
def _stub_browser_probe(monkeypatch):
    """Never launch a real browser from the test suite.

    The launchability probe runs `agent-browser open` + `close --all` for
    real -- which would be slow AND would kill any live agent-browser
    session on this box (e.g. an in-flight research round). All doctor()
    calls in tests get a stubbed probe; probe behavior itself is tested
    directly via _ORIG_PROBE with subprocess mocked.
    """
    monkeypatch.setattr(
        doctor_mod, "_probe_browser_launch", lambda env: (True, "probe stubbed")
    )


def _resolution(command: list[str], source: str, rejected: tuple = ()):
    """Build an AttractorResolution stand-in for doctor-check tests."""
    from amplifier_simulated_user_research.runner import AttractorResolution

    return AttractorResolution(command=command, source=source, rejected=rejected)


def _raise_runtime_error(message: str):
    """Return a resolver stub that fails the way a foreign binary makes it fail."""

    def _stub(checkout):
        raise RuntimeError(message)

    return _stub


def _config(tmp_path, **overrides: Any) -> RoundConfig:
    personas_dir = tmp_path / "personas"
    personas_dir.mkdir(exist_ok=True)
    for name in ("marisol", "dev", "ken"):
        (personas_dir / f"{name}.md").write_text("brief", encoding="utf-8")

    kwargs: dict[str, Any] = dict(
        target_url="http://127.0.0.1:8892",
        seed_command="true",
        seed_cwd=str(tmp_path),
        personas_dir=str(personas_dir),
        output_dir=str(tmp_path / "output"),
        app_source_hint=str(tmp_path),
        personas=["marisol", "dev", "ken"],
        api_key="test-key",
        sur_repo_dir=str(tmp_path),
    )
    kwargs.update(overrides)
    return RoundConfig(**kwargs)


class TestDoctorWithoutConfig:
    def test_returns_environment_checks(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        checks = doctor(None)
        names = [c.name for c in checks]
        assert any("attractor CLI" in n for n in names)
        assert any("agent-browser" in n for n in names)
        assert any("provider key" in n for n in names)
        assert all(isinstance(c, DoctorCheck) for c in checks)

    def test_provider_key_check_fails_when_unset(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        checks = doctor(None)
        provider_check = next(c for c in checks if "provider key" in c.name)
        assert provider_check.ok is False

    def test_provider_key_check_passes_when_set(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        checks = doctor(None)
        provider_check = next(c for c in checks if "provider key" in c.name)
        assert provider_check.ok is True


class TestDoctorWithConfig:
    def test_adds_config_specific_checks(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        config = _config(tmp_path)
        checks = doctor(config)
        names = [c.name for c in checks]
        assert any("personas_dir" in n for n in names)
        assert any("registered" in n for n in names)

    def test_personas_dir_check_ok_when_all_briefs_present(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        config = _config(tmp_path)
        checks = doctor(config)
        personas_check = next(c for c in checks if c.name == "personas_dir")
        assert personas_check.ok is True

    def test_personas_dir_check_fails_when_brief_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        config = _config(tmp_path, personas=["marisol", "dev", "nonexistent-persona"])
        checks = doctor(config)
        personas_check = next(c for c in checks if c.name == "personas_dir")
        assert personas_check.ok is False
        assert "nonexistent-persona" in personas_check.detail

    def test_dot_file_check_against_real_repo(self):
        """Without an override, sur_repo_dir defaults to the real package repo root."""
        checks = doctor(None)
        dot_check = next(c for c in checks if "pipeline .dot" in c.name)
        assert dot_check.ok is True


class TestAttractorCliCheck:
    """Presence is not identity: doctor must FAIL on a foreign `attractor`.

    The live incident: an unrelated package's binary named `attractor` sat
    earlier on PATH; doctor reported [OK] on presence alone and the run died
    later with an inscrutable argparse error.
    """

    def test_fails_on_foreign_binary_with_reason_and_remedy(self, monkeypatch):
        monkeypatch.setattr(
            doctor_mod,
            "resolve_attractor_resolution",
            _raise_runtime_error(
                "no usable attractor engine found. Candidates probed and rejected: "
                "/usr/local/bin/attractor -- `run --help` does not advertise "
                "--param, --logs-root, --on-human-gate. The engine is the "
                "`attractor` console script from amplifier-module-pipeline-runner"
            ),
        )

        check = doctor_mod._check_attractor_cli(None)

        assert check.ok is False
        assert "/usr/local/bin/attractor" in check.detail  # what was found
        assert "--param" in check.detail  # why rejected
        assert "amplifier-module-pipeline-runner" in check.detail  # remedy

    def test_ok_reports_resolved_path_and_source(self, monkeypatch):
        monkeypatch.setattr(
            doctor_mod,
            "resolve_attractor_resolution",
            lambda checkout: _resolution(
                ["/venv/bin/attractor"], "interpreter-sibling"
            ),
        )
        monkeypatch.setattr(
            doctor_mod.shutil, "which", lambda name: "/venv/bin/attractor"
        )

        check = doctor_mod._check_attractor_cli(None)

        assert check.ok is True
        assert check.warn is False
        assert "/venv/bin/attractor" in check.detail
        assert "interpreter-sibling" in check.detail
        assert "identity-validated" in check.detail

    def test_warns_when_a_different_binary_shadows_ours_on_path(self, monkeypatch):
        monkeypatch.setattr(
            doctor_mod,
            "resolve_attractor_resolution",
            lambda checkout: _resolution(
                ["/venv/bin/attractor"], "interpreter-sibling"
            ),
        )
        # PATH's first `attractor` is a DIFFERENT binary than the one we use
        monkeypatch.setattr(
            doctor_mod.shutil, "which", lambda name: "/home/u/.local/bin/attractor"
        )

        check = doctor_mod._check_attractor_cli(None)

        assert check.ok is True  # we routed around it -- not a failure
        assert check.warn is True
        assert "/home/u/.local/bin/attractor" in check.detail
        assert "sibling-first resolution avoided it" in check.detail

    def test_reports_rejected_candidates(self, monkeypatch):
        monkeypatch.setattr(
            doctor_mod,
            "resolve_attractor_resolution",
            lambda checkout: _resolution(
                ["/usr/bin/attractor"],
                "PATH",
                rejected=(("/bad/attractor", "does not advertise --param"),),
            ),
        )
        monkeypatch.setattr(
            doctor_mod.shutil, "which", lambda name: "/usr/bin/attractor"
        )

        check = doctor_mod._check_attractor_cli(None)

        assert check.ok is True
        assert check.warn is True
        assert "/bad/attractor" in check.detail
        assert "does not advertise --param" in check.detail


class TestBrowserLaunchProbe:
    """Exercise the REAL probe function (_ORIG_PROBE) with subprocess mocked."""

    def _fake_run(self, monkeypatch, *, open_rc=0, open_raises=None):
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1] == "open" and open_raises is not None:
                raise open_raises
            proc = subprocess.CompletedProcess(cmd, returncode=0)
            proc.stdout = ""
            proc.stderr = ""
            if cmd[1] == "open":
                proc.returncode = open_rc
                proc.stderr = "could not find browser executable" if open_rc else ""
            return proc

        monkeypatch.setattr(doctor_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(
            doctor_mod.shutil, "which", lambda name: "/fake/agent-browser"
        )
        return calls

    def test_success_opens_and_always_closes(self, monkeypatch):
        calls = self._fake_run(monkeypatch, open_rc=0)
        ok, detail = _ORIG_PROBE({})
        assert ok is True
        assert "launched a real browser" in detail
        assert calls[0][1:] == ["open", "about:blank"]
        assert calls[-1][1:] == ["close", "--all"]  # no state left behind

    def test_nonzero_exit_fails_and_still_closes(self, monkeypatch):
        calls = self._fake_run(monkeypatch, open_rc=2)
        ok, detail = _ORIG_PROBE({})
        assert ok is False
        assert "exited 2" in detail
        assert "could not find browser executable" in detail
        assert calls[-1][1:] == ["close", "--all"]

    def test_timeout_fails_and_still_closes(self, monkeypatch):
        calls = self._fake_run(
            monkeypatch,
            open_raises=subprocess.TimeoutExpired(cmd="agent-browser", timeout=45),
        )
        ok, detail = _ORIG_PROBE({})
        assert ok is False
        assert "timed out" in detail
        assert calls[-1][1:] == ["close", "--all"]

    def test_missing_exe_fails_without_running_anything(self, monkeypatch):
        monkeypatch.setattr(doctor_mod.shutil, "which", lambda name: None)
        ok, detail = _ORIG_PROBE({})
        assert ok is False
        assert "not found on PATH" in detail


class TestDetectPlaywrightHeadlessShell:
    def _fake_home(self, tmp_path, monkeypatch, builds: list[int]):
        home = tmp_path / "home"
        for build in builds:
            binary = (
                home
                / ".cache"
                / "ms-playwright"
                / f"chromium_headless_shell-{build}"
                / "chrome-linux"
                / "headless_shell"
            )
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setattr(doctor_mod.Path, "home", staticmethod(lambda: home))
        return home

    def test_picks_newest_build_numerically(self, tmp_path, monkeypatch):
        self._fake_home(tmp_path, monkeypatch, [999, 1181])
        detected = doctor_mod._detect_playwright_headless_shell()
        assert detected is not None
        assert "chromium_headless_shell-1181" in detected  # 1181 > 999 numerically

    def test_returns_none_when_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            doctor_mod.Path, "home", staticmethod(lambda: tmp_path / "empty-home")
        )
        assert doctor_mod._detect_playwright_headless_shell() is None


class TestBrowserLaunchableCheck:
    def test_fail_carries_remediation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            doctor_mod,
            "_probe_browser_launch",
            lambda env: (
                False,
                "`agent-browser open about:blank` exited 2: no browser",
            ),
        )
        check = doctor_mod._check_browser_launchable(None)
        assert check.ok is False  # FAIL, not warn
        assert check.warn is False
        assert "AGENT_BROWSER_EXECUTABLE_PATH" in check.detail
        assert 'AGENT_BROWSER_ARGS="--no-sandbox"' in check.detail
        assert "agent-browser install" in check.detail
        assert "browser_executable_path" in check.detail  # project.yaml alternative

    def test_remediation_suggests_detected_headless_shell(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            doctor_mod, "_probe_browser_launch", lambda env: (False, "boom")
        )
        monkeypatch.setattr(
            doctor_mod,
            "_detect_playwright_headless_shell",
            lambda: (
                "/home/u/.cache/ms-playwright/chromium_headless_shell-1181/chrome-linux/headless_shell"
            ),
        )
        check = doctor_mod._check_browser_launchable(None)
        assert "chromium_headless_shell-1181" in check.detail

    def test_config_env_overrides_reach_probe(self, tmp_path, monkeypatch):
        binary = tmp_path / "headless_shell"
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        seen_env: dict[str, str] = {}

        def fake_probe(env):
            seen_env.update(env)
            return True, "launched a real browser (open about:blank + close)"

        monkeypatch.setattr(doctor_mod, "_probe_browser_launch", fake_probe)
        config = _config(
            tmp_path,
            browser_executable_path=str(binary),
            browser_args="--no-sandbox",
        )
        check = doctor_mod._check_browser_launchable(config)
        assert check.ok is True
        assert seen_env["AGENT_BROWSER_EXECUTABLE_PATH"] == str(binary)
        assert seen_env["AGENT_BROWSER_ARGS"] == "--no-sandbox"
        assert "project.yaml" in check.detail  # notes the overrides in use

    def test_configured_path_missing_fails_before_probe(self, tmp_path, monkeypatch):
        def exploding_probe(env):  # must never be called
            raise AssertionError("probe should not run when the path is missing")

        monkeypatch.setattr(doctor_mod, "_probe_browser_launch", exploding_probe)
        config = _config(
            tmp_path, browser_executable_path=str(tmp_path / "nonexistent-browser")
        )
        check = doctor_mod._check_browser_launchable(config)
        assert check.ok is False
        assert "not found" in check.detail

    def test_doctor_includes_launchability_check(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        checks = doctor(None)  # probe stubbed by the autouse fixture
        launch_check = next(c for c in checks if c.name == "browser launchable")
        assert launch_check.ok is True


class TestPersonasCustomizedWarning:
    def _shipped_roster(self, tmp_path):
        """Create a fake sur_repo_dir with a shipped personas/ roster."""
        shipped = tmp_path / "repo" / "personas"
        shipped.mkdir(parents=True)
        for name in ("marisol", "dev", "ken"):
            (shipped / f"{name}.md").write_text(
                f"# {name} shipped brief", encoding="utf-8"
            )
        return tmp_path / "repo"

    def test_warns_when_briefs_byte_identical_to_shipped(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        repo = self._shipped_roster(tmp_path)
        config = _config(tmp_path, sur_repo_dir=str(repo))
        # _config wrote generic briefs; overwrite with exact shipped copies
        personas_dir = tmp_path / "personas"
        for name in ("marisol", "dev", "ken"):
            (personas_dir / f"{name}.md").write_bytes(
                (repo / "personas" / f"{name}.md").read_bytes()
            )

        checks = doctor(config)
        custom_check = next(c for c in checks if c.name == "personas customized")
        assert custom_check.ok is True  # a warning, not a failure
        assert custom_check.warn is True
        assert "fiction" in custom_check.detail

    def test_no_warning_when_briefs_customized(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        repo = self._shipped_roster(tmp_path)
        config = _config(tmp_path, sur_repo_dir=str(repo))
        # _config's briefs ("brief") already differ from the shipped copies

        checks = doctor(config)
        custom_check = next(c for c in checks if c.name == "personas customized")
        assert custom_check.ok is True
        assert custom_check.warn is False

    def test_warn_defaults_false_on_all_other_checks(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        config = _config(tmp_path)
        checks = doctor(config)
        for check in checks:
            if check.name != "personas customized":
                assert check.warn is False
