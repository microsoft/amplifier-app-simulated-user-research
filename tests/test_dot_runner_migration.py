"""Regression checks for the dot-runner packaging and bundle migration."""

from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYPROJECTS = (
    ROOT / "pyproject.toml",
    ROOT / "modules" / "tool-simulated-user-research" / "pyproject.toml",
)
_REQUIRED_CLOSURE = (
    "amplifier-module-pipeline-runner",
    "amplifier-foundation",
    "amplifier-module-loop-pipeline[remote]",
    "amplifier-unified-llm-client",
    "amplifier-module-remote-source",
    "amplifier-module-loop-agent",
)


def _dependencies(pyproject: Path) -> list[str]:
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["dependencies"]


def test_each_pyproject_declares_the_full_dot_runner_git_closure():
    for pyproject in PYPROJECTS:
        dependencies = _dependencies(pyproject)
        for requirement in _REQUIRED_CLOSURE:
            matching = [dependency for dependency in dependencies if dependency.startswith(requirement)]
            assert matching, f"{pyproject} is missing {requirement}"
            assert "amplifier-bundle-dot-runner" in matching[0] or requirement == "amplifier-foundation"
        assert not any("amplifier-bundle-attractor" in dependency for dependency in dependencies)


def test_dot_runner_capability_probe_requires_every_forwarded_flag():
    runner = (ROOT / "amplifier_simulated_user_research" / "runner.py").read_text(
        encoding="utf-8"
    )
    for flag in (
        "--param",
        "--provider",
        "--worker",
        "--logs-root",
        "--cwd",
        "--on-human-gate",
    ):
        assert f'"{flag}"' in runner


def test_loop_agent_bundle_manifests_use_dot_runner_sources():
    for manifest in (
        ROOT / "bundles" / "browser-node-agent.yaml",
        ROOT / "simulated-user-research.bundle.md",
    ):
        content = manifest.read_text(encoding="utf-8")
        assert "amplifier-bundle-dot-runner" in content
        assert "amplifier-bundle-attractor" not in content
