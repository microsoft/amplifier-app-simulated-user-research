"""RoundConfig -- per-project configuration for a simulated-user-research round.

This module owns loading/validating the YAML config a project author writes
(see `asur init`). It does NOT contain any
pipeline logic -- it only knows how to turn a project's settings into the
flat `--param key=value` map the attractor `.dot` graph expects. The `.dot`
file (pipelines/simulated-user-research.dot) remains the single source of
truth for stages, prompts, and retry policy (see the DRY rule in the
amplifier-tool-leverage-patterns skill).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_REQUIRED_PERSONA_COUNT = 3

# Placeholder tokens `asur init` writes into the starter project.yaml.
# validate() rejects any value still carrying one -- running a round against
# placeholder settings produces confidently wrong output, not an error.
_SENTINEL_TOKENS = ("REPLACE ME", "REPLACE_ME")
_SENTINEL_MESSAGE = (
    "edit project.yaml before running (asur init wrote placeholder values)"
)


def _package_repo_root() -> Path:
    """Best-effort default for `sur_repo_dir`.

    Two supported layouts, checked in order:

    1. Source checkout: this file lives at
       <repo_root>/amplifier_simulated_user_research/config.py, so the
       grandparent directory is the repo root (contains pipelines/,
       scripts/, personas/, browser-node-agent.yaml).
    2. Wheel install (e.g. `uv tool install git+...`): the same files are
       shipped inside the package under `_bundled/` (see the
       force-include table in pyproject.toml), which then serves as the
       "repo root" -- every consumer resolves paths relative to this
       directory (`<root>/pipelines/...`, `<root>/scripts/...`), so the
       bundled tree is a drop-in substitute.

    A project.yaml `sur_repo_dir` or CLI flag always overrides both.
    """
    package_dir = Path(__file__).resolve().parent
    checkout = package_dir.parent
    if (checkout / "pipelines" / "simulated-user-research.dot").is_file():
        return checkout
    bundled = package_dir / "_bundled"
    if (bundled / "pipelines" / "simulated-user-research.dot").is_file():
        return bundled
    # Neither layout found -- return the checkout guess; doctor()'s
    # "pipeline .dot present" check reports the problem loudly.
    return checkout


@dataclass
class RoundConfig:
    """Everything one research round needs, independent of how it's invoked.

    Loaded from a project's YAML file (see `RoundConfig.from_yaml`) or built
    directly. Every field here maps to a `.dot` graph param -- see
    `to_dot_params()` -- or to run-harness plumbing (logs_root, provider).
    """

    target_url: str
    seed_command: str
    seed_cwd: str
    personas_dir: str
    output_dir: str
    app_source_hint: str
    personas: list[str] = field(default_factory=list)
    api_key: str | None = None
    api_key_env: str | None = None
    browser_bundle: str = "sur-browser-node"
    sur_repo_dir: str | None = None
    provider: str = "anthropic"
    logs_root: str | None = None
    # Optional custom-browser passthrough (e.g. Linux ARM64, where
    # agent-browser's managed Chrome-for-Testing channel ships no builds and
    # `agent-browser install` exits 2 -- see README "ARM64 / custom browser").
    # run_round() exports these into the pipeline subprocess environment as
    # AGENT_BROWSER_EXECUTABLE_PATH / AGENT_BROWSER_ARGS, so the project.yaml
    # can carry the fix instead of the caller's shell.
    browser_executable_path: str | None = None
    browser_args: str | None = None
    # Optional escape hatch for local development against an unmerged /
    # not-yet-released attractor checkout. When set, the runner shells out
    # via `uv run --project <attractor_checkout>/modules/pipeline-runner
    # attractor ...` instead of invoking the `attractor` console script this
    # package installs as a normal git-subdirectory dependency. Leave unset
    # (the default) for normal use.
    attractor_checkout: str | None = None

    @classmethod
    def from_yaml(cls, path: str | Path) -> "RoundConfig":
        """Load a RoundConfig from a project YAML file.

        Raises:
            FileNotFoundError: if `path` does not exist.
            ValueError: if the YAML doesn't parse to a mapping, or contains
                unknown keys (fail loud on typos rather than silently
                ignoring them).
        """
        p = Path(path).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"config file not found: {p}")

        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ValueError(
                f"{p}: expected a YAML mapping at the top level, got {type(raw).__name__}"
            )

        known_fields = {f for f in cls.__dataclass_fields__}
        unknown = set(raw) - known_fields
        if unknown:
            raise ValueError(
                f"{p}: unknown config key(s): {', '.join(sorted(unknown))}"
            )

        return cls(**raw)

    def resolved_api_key(self) -> str:
        """Resolve the app credential from api_key or api_key_env.

        This is the TARGET APPLICATION's login credential (what the persona
        browser sessions use to authenticate) -- it is unrelated to the LLM
        provider's API key (e.g. ANTHROPIC_API_KEY), which must separately
        be present in the process environment for the `attractor` CLI's own
        preflight check (see `doctor.py`).

        Raises:
            ValueError: if neither api_key nor a resolvable api_key_env is set.
        """
        import os

        if self.api_key:
            return self.api_key
        if self.api_key_env:
            value = os.environ.get(self.api_key_env)
            if value:
                return value
            raise ValueError(
                f"api_key_env={self.api_key_env!r} is not set in the environment"
            )
        raise ValueError("RoundConfig needs either api_key or api_key_env")

    def browser_env(self) -> dict[str, str]:
        """Env vars for a custom browser binary, ready to merge into a subprocess env.

        Empty dict when neither key is configured (the common case --
        agent-browser's own managed browser is used). run_round() merges
        this over os.environ for the pipeline subprocess; doctor()'s
        launchability probe uses the same merge so it tests exactly what a
        run would use.
        """
        env: dict[str, str] = {}
        if self.browser_executable_path:
            env["AGENT_BROWSER_EXECUTABLE_PATH"] = str(
                Path(self.browser_executable_path).expanduser()
            )
        if self.browser_args:
            env["AGENT_BROWSER_ARGS"] = self.browser_args
        return env

    def resolved_sur_repo_dir(self) -> Path:
        """Absolute path to the amplifier-app-simulated-user-research repo root."""
        return (
            Path(self.sur_repo_dir).expanduser()
            if self.sur_repo_dir
            else _package_repo_root()
        )

    def validate(self) -> list[str]:
        """Return a list of human-readable problems; empty means valid.

        Checks structure and required fields only -- it does NOT touch the
        filesystem or network (that's `doctor()`'s job). Cheap and safe to
        call before every run.
        """
        problems: list[str] = []

        if not self.target_url:
            problems.append("target_url is required")
        if not self.seed_command:
            problems.append("seed_command is required")
        if not self.seed_cwd:
            problems.append("seed_cwd is required")
        if not self.personas_dir:
            problems.append("personas_dir is required")
        if not self.output_dir:
            problems.append("output_dir is required")
        if not self.app_source_hint:
            problems.append("app_source_hint is required")

        if len(self.personas) != _REQUIRED_PERSONA_COUNT:
            problems.append(
                f"personas must list exactly {_REQUIRED_PERSONA_COUNT} names "
                f"(the .dot graph has exactly persona1/persona2/persona3 nodes), "
                f"got {len(self.personas)}: {self.personas!r}"
            )

        if not self.api_key and not self.api_key_env:
            problems.append("one of api_key or api_key_env is required")
        if self.api_key and self.api_key_env:
            problems.append("set only one of api_key or api_key_env, not both")

        if self.provider not in {"anthropic", "openai", "gemini"}:
            problems.append(
                f"unknown provider {self.provider!r} (known: anthropic, openai, gemini)"
            )

        sentinel_fields = self._fields_with_sentinels()
        if sentinel_fields:
            problems.append(
                f"{_SENTINEL_MESSAGE}: placeholder found in {', '.join(sentinel_fields)}"
            )

        return problems

    def _fields_with_sentinels(self) -> list[str]:
        """Names of fields still carrying an `asur init` placeholder token."""
        values: dict[str, str] = {
            "target_url": self.target_url,
            "seed_command": self.seed_command,
            "seed_cwd": self.seed_cwd,
            "personas_dir": self.personas_dir,
            "output_dir": self.output_dir,
            "app_source_hint": self.app_source_hint,
            "api_key": self.api_key or "",
            "api_key_env": self.api_key_env or "",
            "browser_bundle": self.browser_bundle,
            "sur_repo_dir": self.sur_repo_dir or "",
            "browser_executable_path": self.browser_executable_path or "",
            "browser_args": self.browser_args or "",
        }
        for i, persona in enumerate(self.personas):
            values[f"personas[{i}]"] = persona

        return [
            name
            for name, value in values.items()
            if any(token in value for token in _SENTINEL_TOKENS)
        ]

    def to_dot_params(self) -> dict[str, str]:
        """Build the flat `--param key=value` map the .dot graph expects.

        Raises the same errors `resolved_api_key()`/`validate()` would --
        call `validate()` first if you want problems reported as a list
        instead of the first exception.
        """
        p1, p2, p3 = self.personas
        return {
            "target_url": self.target_url,
            "api_key": self.resolved_api_key(),
            "seed_command": self.seed_command,
            "personas_dir": str(Path(self.personas_dir).expanduser()),
            "output_dir": str(Path(self.output_dir).expanduser()),
            "app_source_hint": self.app_source_hint,
            "persona1": p1,
            "persona2": p2,
            "persona3": p3,
            "sur_repo_dir": str(self.resolved_sur_repo_dir()),
            "browser_bundle": self.browser_bundle,
        }

    def to_yaml_dict(self) -> dict[str, Any]:
        """Serialize back to a plain dict suitable for `yaml.safe_dump` (used by `init`)."""
        return {
            "target_url": self.target_url,
            "api_key": self.api_key,
            "api_key_env": self.api_key_env,
            "seed_command": self.seed_command,
            "seed_cwd": self.seed_cwd,
            "personas_dir": self.personas_dir,
            "output_dir": self.output_dir,
            "app_source_hint": self.app_source_hint,
            "personas": self.personas,
            "browser_bundle": self.browser_bundle,
            "sur_repo_dir": self.sur_repo_dir,
            "provider": self.provider,
            "logs_root": self.logs_root,
            "browser_executable_path": self.browser_executable_path,
            "browser_args": self.browser_args,
            "attractor_checkout": self.attractor_checkout,
        }
