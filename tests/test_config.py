"""Tests for RoundConfig: loading, validation, and .dot param mapping."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from amplifier_simulated_user_research.config import RoundConfig


def _minimal_kwargs(**overrides) -> dict:
    kwargs = dict(
        target_url="http://127.0.0.1:8892",
        seed_command="python3 seed.py",
        seed_cwd="/tmp/seed-cwd",
        personas_dir="/tmp/personas",
        output_dir="/tmp/output",
        app_source_hint="/tmp/app/src",
        personas=["marisol", "dev", "ken"],
        api_key="research-key",
    )
    kwargs.update(overrides)
    return kwargs


class TestRoundConfigValidate:
    def test_valid_config_has_no_problems(self):
        config = RoundConfig(**_minimal_kwargs())
        assert config.validate() == []

    def test_missing_required_fields_reported(self):
        config = RoundConfig(**_minimal_kwargs(target_url="", seed_command=""))
        problems = config.validate()
        assert any("target_url" in p for p in problems)
        assert any("seed_command" in p for p in problems)

    def test_wrong_persona_count_reported(self):
        config = RoundConfig(**_minimal_kwargs(personas=["marisol", "dev"]))
        problems = config.validate()
        assert any("exactly 3" in p for p in problems)

    def test_missing_api_key_and_env_reported(self):
        config = RoundConfig(**_minimal_kwargs(api_key=None))
        problems = config.validate()
        assert any("api_key" in p for p in problems)

    def test_both_api_key_and_env_reported(self):
        config = RoundConfig(**_minimal_kwargs(api_key="x", api_key_env="Y"))
        problems = config.validate()
        assert any("only one" in p for p in problems)

    def test_unknown_provider_reported(self):
        config = RoundConfig(**_minimal_kwargs(provider="not-a-provider"))
        problems = config.validate()
        assert any("unknown provider" in p for p in problems)


class TestRoundConfigApiKey:
    def test_resolves_from_api_key(self):
        config = RoundConfig(**_minimal_kwargs(api_key="literal-key"))
        assert config.resolved_api_key() == "literal-key"

    def test_resolves_from_env(self, monkeypatch):
        monkeypatch.setenv("SUR_TEST_API_KEY", "env-key")
        config = RoundConfig(
            **_minimal_kwargs(api_key=None, api_key_env="SUR_TEST_API_KEY")
        )
        assert config.resolved_api_key() == "env-key"

    def test_raises_when_env_var_unset(self, monkeypatch):
        monkeypatch.delenv("SUR_TEST_API_KEY_MISSING", raising=False)
        config = RoundConfig(
            **_minimal_kwargs(api_key=None, api_key_env="SUR_TEST_API_KEY_MISSING")
        )
        with pytest.raises(ValueError, match="is not set"):
            config.resolved_api_key()

    def test_raises_when_neither_set(self):
        config = RoundConfig(**_minimal_kwargs(api_key=None, api_key_env=None))
        with pytest.raises(ValueError, match="needs either"):
            config.resolved_api_key()


class TestRoundConfigToDotParams:
    def test_maps_personas_to_persona123(self):
        config = RoundConfig(**_minimal_kwargs(personas=["marisol", "dev", "ken"]))
        params = config.to_dot_params()
        assert params["persona1"] == "marisol"
        assert params["persona2"] == "dev"
        assert params["persona3"] == "ken"

    def test_includes_all_required_dot_params(self):
        config = RoundConfig(**_minimal_kwargs())
        params = config.to_dot_params()
        expected_keys = {
            "target_url",
            "api_key",
            "seed_command",
            "personas_dir",
            "output_dir",
            "app_source_hint",
            "persona1",
            "persona2",
            "persona3",
            "sur_repo_dir",
            "browser_bundle",
        }
        assert expected_keys <= set(params)

    def test_sur_repo_dir_defaults_to_package_repo_root(self):
        config = RoundConfig(**_minimal_kwargs())
        params = config.to_dot_params()
        repo_root = Path(params["sur_repo_dir"])
        assert (repo_root / "pipelines" / "simulated-user-research.dot").is_file()
        assert (repo_root / "scripts" / "run_browser_node.py").is_file()

    def test_sur_repo_dir_override_respected(self):
        config = RoundConfig(**_minimal_kwargs(sur_repo_dir="/custom/repo/dir"))
        params = config.to_dot_params()
        assert params["sur_repo_dir"] == "/custom/repo/dir"


class TestRoundConfigFromYaml:
    def test_loads_from_yaml(self, tmp_path):
        data = _minimal_kwargs()
        config_path = tmp_path / "project.yaml"
        config_path.write_text(yaml.safe_dump(data), encoding="utf-8")

        config = RoundConfig.from_yaml(config_path)
        assert config.target_url == data["target_url"]
        assert config.personas == data["personas"]

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            RoundConfig.from_yaml(tmp_path / "nope.yaml")

    def test_non_mapping_yaml_raises(self, tmp_path):
        config_path = tmp_path / "project.yaml"
        config_path.write_text("- just\n- a\n- list\n", encoding="utf-8")
        with pytest.raises(ValueError, match="mapping"):
            RoundConfig.from_yaml(config_path)

    def test_unknown_key_raises(self, tmp_path):
        data = _minimal_kwargs()
        data["totally_unknown_field"] = "oops"
        config_path = tmp_path / "project.yaml"
        config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
        with pytest.raises(ValueError, match="unknown config key"):
            RoundConfig.from_yaml(config_path)


class TestRoundConfigToYamlDict:
    def test_round_trips_through_yaml(self, tmp_path):
        config = RoundConfig(**_minimal_kwargs())
        config_path = tmp_path / "project.yaml"
        config_path.write_text(yaml.safe_dump(config.to_yaml_dict()), encoding="utf-8")

        reloaded = RoundConfig.from_yaml(config_path)
        assert reloaded.target_url == config.target_url
        assert reloaded.personas == config.personas
