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


class TestSentinelValidation:
    def test_replace_me_in_seed_command_rejected(self):
        config = RoundConfig(
            **_minimal_kwargs(seed_command="echo 'REPLACE ME: seed your instance'")
        )
        problems = config.validate()
        assert any(
            "edit project.yaml before running "
            "(amplifier-simulated-user-research init wrote placeholder values)" in p
            for p in problems
        )
        assert any("seed_command" in p for p in problems)

    def test_replace_me_underscore_in_api_key_env_rejected(self):
        config = RoundConfig(
            **_minimal_kwargs(api_key=None, api_key_env="REPLACE_ME_API_KEY_ENV_VAR")
        )
        problems = config.validate()
        assert any("placeholder" in p for p in problems)
        assert any("api_key_env" in p for p in problems)

    def test_replace_me_in_paths_rejected(self):
        config = RoundConfig(
            **_minimal_kwargs(
                seed_cwd="/REPLACE_ME/absolute/path/to/your/app",
                app_source_hint="/REPLACE_ME/absolute/path/to/your/app/src",
            )
        )
        problems = config.validate()
        offending = [p for p in problems if "placeholder" in p]
        assert len(offending) == 1
        assert "seed_cwd" in offending[0] and "app_source_hint" in offending[0]

    def test_replace_me_in_persona_name_rejected(self):
        config = RoundConfig(
            **_minimal_kwargs(personas=["marisol", "dev", "REPLACE_ME"])
        )
        problems = config.validate()
        assert any("personas[2]" in p for p in problems)

    def test_clean_config_has_no_sentinel_problem(self):
        config = RoundConfig(**_minimal_kwargs())
        assert not any("placeholder" in p for p in config.validate())

    def test_fresh_init_starter_config_is_rejected(self):
        """The exact config `amplifier-simulated-user-research init` writes must fail validation until edited."""
        from amplifier_simulated_user_research.cli import _build_starter_config

        problems = _build_starter_config().validate()
        assert any(
            "edit project.yaml before running "
            "(amplifier-simulated-user-research init wrote placeholder values)" in p
            for p in problems
        )


class TestPackageRepoRoot:
    """_package_repo_root() supports both layouts: source checkout and
    wheel install (files force-included under <package>/_bundled)."""

    def _fake_config_at(self, monkeypatch, config_py: Path):
        import amplifier_simulated_user_research.config as config_module

        config_py.parent.mkdir(parents=True, exist_ok=True)
        config_py.write_text("# fake\n", encoding="utf-8")
        monkeypatch.setattr(config_module, "__file__", str(config_py))
        return config_module

    def test_prefers_source_checkout_layout(self, tmp_path, monkeypatch):
        repo = tmp_path / "checkout"
        dot = repo / "pipelines" / "simulated-user-research.dot"
        dot.parent.mkdir(parents=True)
        dot.write_text("digraph {}", encoding="utf-8")
        config_module = self._fake_config_at(
            monkeypatch, repo / "amplifier_simulated_user_research" / "config.py"
        )

        assert config_module._package_repo_root() == repo

    def test_falls_back_to_bundled_tree_for_wheel_installs(self, tmp_path, monkeypatch):
        pkg = tmp_path / "site-packages" / "amplifier_simulated_user_research"
        bundled_dot = pkg / "_bundled" / "pipelines" / "simulated-user-research.dot"
        bundled_dot.parent.mkdir(parents=True)
        bundled_dot.write_text("digraph {}", encoding="utf-8")
        config_module = self._fake_config_at(monkeypatch, pkg / "config.py")

        assert config_module._package_repo_root() == pkg / "_bundled"

    def test_browser_bundle_resolves_under_bundles_subdir(self, tmp_path, monkeypatch):
        """The browser bundle yaml lives in bundles/ -- a PACKAGE-FREE dir.

        Colocating it with pyproject.toml made the Amplifier activator
        editable-install this entire project into the CLI's venv (and die on
        dependency-pin conflicts), breaking every browser stage. Both layouts
        must expose it at <root>/bundles/browser-node-agent.yaml.
        """
        pkg = tmp_path / "site-packages" / "amplifier_simulated_user_research"
        bundled = pkg / "_bundled"
        (bundled / "pipelines").mkdir(parents=True)
        (bundled / "pipelines" / "simulated-user-research.dot").write_text(
            "digraph {}", encoding="utf-8"
        )
        yaml_path = bundled / "bundles" / "browser-node-agent.yaml"
        yaml_path.parent.mkdir(parents=True)
        yaml_path.write_text("bundle:\n  name: x\n", encoding="utf-8")
        config_module = self._fake_config_at(monkeypatch, pkg / "config.py")

        root = config_module._package_repo_root()
        assert (root / "bundles" / "browser-node-agent.yaml").is_file()
        # and the bundle's own directory must never contain a pyproject.toml
        assert not (root / "bundles" / "pyproject.toml").exists()

    def test_shipped_repo_layout_keeps_bundle_yaml_package_free(self):
        """Guard the real checkout: bundles/ must stay package-free."""
        import amplifier_simulated_user_research.config as config_module

        root = config_module._package_repo_root()
        assert (root / "bundles" / "browser-node-agent.yaml").is_file()
        assert not (root / "bundles" / "pyproject.toml").exists()

    def test_returns_checkout_guess_when_neither_layout_found(
        self, tmp_path, monkeypatch
    ):
        pkg = tmp_path / "nowhere" / "amplifier_simulated_user_research"
        config_module = self._fake_config_at(monkeypatch, pkg / "config.py")

        # doctor()'s ".dot present" check reports this loudly downstream
        assert config_module._package_repo_root() == tmp_path / "nowhere"


class TestBrowserEnv:
    def test_empty_by_default(self):
        config = RoundConfig(**_minimal_kwargs())
        assert config.browser_env() == {}

    def test_exports_both_vars_when_configured(self):
        config = RoundConfig(
            **_minimal_kwargs(
                browser_executable_path="/opt/pw/chromium_headless_shell-1181/chrome-linux/headless_shell",
                browser_args="--no-sandbox",
            )
        )
        assert config.browser_env() == {
            "AGENT_BROWSER_EXECUTABLE_PATH": "/opt/pw/chromium_headless_shell-1181/chrome-linux/headless_shell",
            "AGENT_BROWSER_ARGS": "--no-sandbox",
        }

    def test_expands_user_in_executable_path(self):
        config = RoundConfig(
            **_minimal_kwargs(browser_executable_path="~/bin/headless_shell")
        )
        path = config.browser_env()["AGENT_BROWSER_EXECUTABLE_PATH"]
        assert "~" not in path and path.endswith("/bin/headless_shell")

    def test_round_trips_through_yaml(self, tmp_path):
        config = RoundConfig(
            **_minimal_kwargs(
                browser_executable_path="/opt/headless_shell",
                browser_args="--no-sandbox",
            )
        )
        config_path = tmp_path / "project.yaml"
        config_path.write_text(yaml.safe_dump(config.to_yaml_dict()), encoding="utf-8")

        reloaded = RoundConfig.from_yaml(config_path)
        assert reloaded.browser_executable_path == "/opt/headless_shell"
        assert reloaded.browser_args == "--no-sandbox"


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


class TestResetCommand:
    """Optional stage-0 reset (see the .dot's STAGE 0 contract).

    An un-reset test fixture corrupted two consecutive real rounds -- every
    "triage is broken" finding traced back to a queue a human had worked
    through days earlier. The param exists to prevent that; being OPTIONAL
    and absent-by-default is equally load-bearing, since most projects have
    no reset step and must never be forced to invent one.
    """

    def test_present_is_passed_through(self):
        config = RoundConfig(
            **_minimal_kwargs(reset_command="python3 scripts/reset_research.py --yes")
        )
        params = config.to_dot_params()
        assert params["reset_command"] == "python3 scripts/reset_research.py --yes"

    def test_absent_is_omitted_entirely(self):
        """Not passed as an empty string -- omitted, so the graph no-ops."""
        config = RoundConfig(**_minimal_kwargs())
        assert config.reset_command is None
        assert "reset_command" not in config.to_dot_params()

    def test_empty_string_treated_as_absent(self):
        config = RoundConfig(**_minimal_kwargs(reset_command=""))
        assert "reset_command" not in config.to_dot_params()

    def test_absent_reset_command_is_valid(self):
        """Omitting it is legitimate -- it must not trip validation."""
        assert RoundConfig(**_minimal_kwargs()).validate() == []
        assert RoundConfig(**_minimal_kwargs(reset_command="")).validate() == []

    def test_set_but_placeholder_is_rejected(self):
        """A reset that doesn't really reset is the bug this stage prevents."""
        config = RoundConfig(
            **_minimal_kwargs(reset_command="REPLACE_ME: your reset command")
        )
        problems = config.validate()
        assert any("placeholder" in p for p in problems)
        assert any("reset_command" in p for p in problems)

    def test_does_not_disturb_the_other_params(self):
        """Mirrors seed_command: additive only, nothing else shifts."""
        without = RoundConfig(**_minimal_kwargs()).to_dot_params()
        with_reset = RoundConfig(
            **_minimal_kwargs(reset_command="make reset")
        ).to_dot_params()
        assert set(with_reset) - set(without) == {"reset_command"}
        assert {k: v for k, v in with_reset.items() if k != "reset_command"} == without

    def test_yaml_round_trip(self, tmp_path):
        config = RoundConfig(**_minimal_kwargs(reset_command="make reset-research"))
        config_path = tmp_path / "project.yaml"
        config_path.write_text(yaml.safe_dump(config.to_yaml_dict()), encoding="utf-8")

        reloaded = RoundConfig.from_yaml(config_path)

        assert reloaded.reset_command == "make reset-research"
        assert reloaded.to_dot_params()["reset_command"] == "make reset-research"

    def test_yaml_round_trip_when_unset(self, tmp_path):
        config = RoundConfig(**_minimal_kwargs())
        config_path = tmp_path / "project.yaml"
        config_path.write_text(yaml.safe_dump(config.to_yaml_dict()), encoding="utf-8")

        reloaded = RoundConfig.from_yaml(config_path)

        assert reloaded.reset_command is None
        assert "reset_command" not in reloaded.to_dot_params()

    def test_key_absent_from_yaml_loads_fine(self, tmp_path):
        """A pre-existing project.yaml (written before this feature) still loads."""
        data = _minimal_kwargs()
        assert "reset_command" not in data
        config_path = tmp_path / "project.yaml"
        config_path.write_text(yaml.safe_dump(data), encoding="utf-8")

        config = RoundConfig.from_yaml(config_path)

        assert config.reset_command is None
        assert config.validate() == []


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
