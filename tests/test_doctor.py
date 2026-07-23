"""Tests for doctor(): environment diagnostics, all filesystem/subprocess mocked."""

from __future__ import annotations

from typing import Any

from amplifier_simulated_user_research.config import RoundConfig
from amplifier_simulated_user_research.doctor import DoctorCheck, doctor


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
