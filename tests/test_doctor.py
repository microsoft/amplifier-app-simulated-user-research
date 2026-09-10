"""Tests for dot-runner-facing environment diagnostics."""

from __future__ import annotations

import importlib
import subprocess

import pytest
from amplifier_simulated_user_research.config import RoundConfig
from amplifier_simulated_user_research.doctor import DoctorCheck, doctor
from amplifier_simulated_user_research.runner import DotRunnerResolution

# The package re-exports the doctor function, so import the submodule explicitly.
doctor_mod = importlib.import_module("amplifier_simulated_user_research.doctor")
_ORIGINAL_BROWSER_PROBE = doctor_mod._probe_browser_launch


@pytest.fixture(autouse=True)
def _stub_browser_probe(monkeypatch):
    """Keep doctor() unit tests from opening or closing a real browser."""
    monkeypatch.setattr(
        doctor_mod, "_probe_browser_launch", lambda env: (True, "probe stubbed")
    )


def _config(tmp_path, **overrides) -> RoundConfig:
    personas_dir = tmp_path / "personas"
    personas_dir.mkdir(exist_ok=True)
    for name in ("marisol", "dev", "ken"):
        (personas_dir / f"{name}.md").write_text("brief", encoding="utf-8")
    kwargs = {
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


class TestDotRunnerCliCheck:
    def test_reports_resolved_path_and_source(self, monkeypatch):
        monkeypatch.setattr(
            doctor_mod,
            "resolve_dot_runner_resolution",
            lambda checkout: DotRunnerResolution(
                command=["/venv/bin/dot-runner"], source="interpreter-sibling"
            ),
        )
        monkeypatch.setattr(doctor_mod.shutil, "which", lambda name: "/venv/bin/dot-runner")

        check = doctor_mod._check_dot_runner_cli(None)

        assert check == DoctorCheck(
            "dot-runner CLI",
            True,
            "resolved: /venv/bin/dot-runner (via interpreter-sibling); identity-validated",
        )

    def test_passes_dot_runner_checkout_to_resolver(self, tmp_path, monkeypatch):
        seen = []
        monkeypatch.setattr(
            doctor_mod,
            "resolve_dot_runner_resolution",
            lambda checkout: seen.append(checkout)
            or DotRunnerResolution(command=["dot-runner"], source="checkout"),
        )

        check = doctor_mod._check_dot_runner_cli(
            _config(tmp_path, dot_runner_checkout="/src/dot-runner")
        )

        assert check.ok is True
        assert seen == ["/src/dot-runner"]

    def test_fails_with_resolver_diagnosis(self, monkeypatch):
        monkeypatch.setattr(
            doctor_mod,
            "resolve_dot_runner_resolution",
            lambda checkout: (_ for _ in ()).throw(
                RuntimeError("no usable dot-runner engine found")
            ),
        )

        check = doctor_mod._check_dot_runner_cli(None)

        assert check.ok is False
        assert check.name == "dot-runner CLI"
        assert "no usable dot-runner engine" in check.detail

    def test_warns_when_path_shadows_the_resolved_binary(self, monkeypatch):
        monkeypatch.setattr(
            doctor_mod,
            "resolve_dot_runner_resolution",
            lambda checkout: DotRunnerResolution(
                command=["/venv/bin/dot-runner"], source="interpreter-sibling"
            ),
        )
        monkeypatch.setattr(
            doctor_mod.shutil, "which", lambda name: "/usr/local/bin/dot-runner"
        )

        check = doctor_mod._check_dot_runner_cli(None)

        assert check.ok is True
        assert check.warn is True
        assert "sibling-first resolution avoided it" in check.detail


class TestDoctor:
    def test_reports_dot_runner_without_launching_a_real_browser(self, monkeypatch):
        monkeypatch.setattr(
            doctor_mod,
            "resolve_dot_runner_resolution",
            lambda checkout: DotRunnerResolution(command=["dot-runner"], source="PATH"),
        )
        monkeypatch.setattr(doctor_mod, "_probe_browser_launch", lambda env: (True, "stubbed"))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        checks = doctor()

        assert any(check.name == "dot-runner CLI" for check in checks)
        assert all(isinstance(check, DoctorCheck) for check in checks)

    def test_configured_missing_browser_fails_before_probe(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            doctor_mod,
            "_probe_browser_launch",
            lambda env: (_ for _ in ()).throw(AssertionError("probe must not run")),
        )

        check = doctor_mod._check_browser_launchable(
            _config(tmp_path, browser_executable_path=str(tmp_path / "missing-browser"))
        )

        assert check.ok is False
        assert "configured browser_executable_path not found" in check.detail

    def test_configured_doctor_includes_all_config_specific_checks(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            doctor_mod,
            "resolve_dot_runner_resolution",
            lambda checkout: DotRunnerResolution(command=["dot-runner"], source="PATH"),
        )
        monkeypatch.setattr(
            doctor_mod,
            "_check_browser_bundle_registered",
            lambda bundle: DoctorCheck(f"bundle {bundle!r} registered", True, "stubbed"),
        )
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        checks = doctor(_config(tmp_path, personas=["marisol", "missing", "ken"]))

        by_name = {check.name: check for check in checks}
        assert "dot-runner CLI" in by_name
        assert "personas_dir" in by_name
        assert "simulated-user-research-browser-node' registered" in " ".join(by_name)
        assert by_name["personas_dir"].ok is False
        assert "personas customized" in by_name
        assert "anthropic provider key (ANTHROPIC_API_KEY)" in by_name

    def test_configured_doctor_uses_the_selected_provider_key(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            doctor_mod,
            "resolve_dot_runner_resolution",
            lambda checkout: DotRunnerResolution(command=["dot-runner"], source="PATH"),
        )
        monkeypatch.setattr(
            doctor_mod,
            "_check_browser_bundle_registered",
            lambda bundle: DoctorCheck(f"bundle {bundle!r} registered", True, "stubbed"),
        )
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")

        checks = doctor(_config(tmp_path, provider="openai"))

        by_name = {check.name: check for check in checks}
        assert by_name["openai provider key (OPENAI_API_KEY)"].ok is True


class TestBrowserLaunchProbe:
    def _fake_run(self, monkeypatch, *, open_returncode=0, open_error=None):
        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if command[1] == "open" and open_error is not None:
                raise open_error
            result = subprocess.CompletedProcess(command, open_returncode)
            result.stdout = ""
            result.stderr = "browser unavailable" if open_returncode else ""
            return result

        monkeypatch.setattr(doctor_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(
            doctor_mod.shutil, "which", lambda name: "/fake/agent-browser"
        )
        return calls

    def test_successfully_opened_browser_is_always_closed(self, monkeypatch):
        calls = self._fake_run(monkeypatch)

        ok, detail = _ORIGINAL_BROWSER_PROBE({})

        assert ok is True
        assert "launched a real browser" in detail
        assert calls == [
            ["/fake/agent-browser", "open", "about:blank"],
            ["/fake/agent-browser", "close", "--all"],
        ]

    def test_failed_browser_open_is_still_closed(self, monkeypatch):
        calls = self._fake_run(monkeypatch, open_returncode=2)

        ok, detail = _ORIGINAL_BROWSER_PROBE({})

        assert ok is False
        assert "exited 2" in detail
        assert calls[-1] == ["/fake/agent-browser", "close", "--all"]

    def test_timed_out_browser_open_is_still_closed(self, monkeypatch):
        calls = self._fake_run(
            monkeypatch,
            open_error=subprocess.TimeoutExpired("agent-browser", 45),
        )

        ok, detail = _ORIGINAL_BROWSER_PROBE({})

        assert ok is False
        assert "timed out" in detail
        assert calls[-1] == ["/fake/agent-browser", "close", "--all"]

    def test_missing_browser_does_not_invoke_subprocess(self, monkeypatch):
        monkeypatch.setattr(doctor_mod.shutil, "which", lambda name: None)

        ok, detail = _ORIGINAL_BROWSER_PROBE({})

        assert ok is False
        assert detail == "agent-browser not found on PATH"


class TestBrowserRemediation:
    def test_detects_the_highest_playwright_build_numerically(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        for build in (999, 1181):
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

        assert "chromium_headless_shell-1181" in (
            doctor_mod._detect_playwright_headless_shell() or ""
        )

    def test_failed_probe_includes_custom_browser_remediation(self, monkeypatch):
        monkeypatch.setattr(doctor_mod, "_probe_browser_launch", lambda env: (False, "boom"))

        check = doctor_mod._check_browser_launchable(None)

        assert check.ok is False
        assert "AGENT_BROWSER_EXECUTABLE_PATH" in check.detail
        assert 'AGENT_BROWSER_ARGS="--no-sandbox"' in check.detail

    def test_configured_browser_environment_reaches_the_probe(self, tmp_path, monkeypatch):
        browser = tmp_path / "headless_shell"
        browser.write_text("#!/bin/sh\n", encoding="utf-8")
        received: dict[str, str] = {}
        monkeypatch.setattr(
            doctor_mod,
            "_probe_browser_launch",
            lambda env: (received.update(env) or True, "launched"),
        )

        check = doctor_mod._check_browser_launchable(
            _config(
                tmp_path,
                browser_executable_path=str(browser),
                browser_args="--no-sandbox",
            )
        )

        assert check.ok is True
        assert received["AGENT_BROWSER_EXECUTABLE_PATH"] == str(browser)
        assert received["AGENT_BROWSER_ARGS"] == "--no-sandbox"


class TestDoctorWarnings:
    def test_stale_installed_build_is_a_nonblocking_warning(self, monkeypatch):
        from amplifier_simulated_user_research.provenance import StalenessResult

        monkeypatch.setattr(
            doctor_mod,
            "check_installed_build_staleness",
            lambda config: StalenessResult(
                "stale",
                "/src/checkout",
                "config sur_repo_dir",
                ("wrapper_sha256: installed=old checkout=new",),
                "installed build differs from the checkout",
            ),
        )

        check = doctor_mod._check_installed_build_current(None)

        assert check.ok is True
        assert check.warn is True

    def test_undetermined_installed_build_is_not_a_warning(self, monkeypatch):
        from amplifier_simulated_user_research.provenance import StalenessResult

        monkeypatch.setattr(
            doctor_mod,
            "check_installed_build_staleness",
            lambda config: StalenessResult(
                "undetermined", None, None, (), "no local git checkout found"
            ),
        )

        check = doctor_mod._check_installed_build_current(None)

        assert check.ok is True
        assert check.warn is False

    def test_byte_identical_personas_produce_a_warning(self, tmp_path):
        repo = tmp_path / "repo"
        shipped = repo / "personas"
        configured = tmp_path / "configured"
        shipped.mkdir(parents=True)
        configured.mkdir()
        for name in ("marisol", "dev", "ken"):
            content = f"# {name}\n"
            (shipped / f"{name}.md").write_text(content, encoding="utf-8")
            (configured / f"{name}.md").write_text(content, encoding="utf-8")

        check = doctor_mod._check_personas_customized(
            str(configured), ["marisol", "dev", "ken"], repo
        )

        assert check.ok is True
        assert check.warn is True
        assert "fiction" in check.detail
