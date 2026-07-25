"""Amplifier tool module: simulated-user-research.

Thin agent-callable wrapper over the `amplifier_simulated_user_research`
lib (L2) -- see that package for `RoundConfig` / `run_round` / `doctor`.
This module adds NO pipeline logic; the pipeline's `.dot` graph
(pipelines/simulated-user-research.dot) remains the single logic home. Both
tools here run their blocking lib call via `asyncio.to_thread` since
`run_research_round` in particular can block for many minutes (a real
attractor pipeline round with LLM + browser sessions).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Any

from amplifier_core import ToolResult

logger = logging.getLogger(__name__)


class RunResearchRoundTool:
    """Run one simulated-user-research round from a project.yaml config."""

    @property
    def name(self) -> str:
        return "run_research_round"

    @property
    def description(self) -> str:
        return (
            "Run one user-research-style AUDIT round: seeds a scratch instance, "
            "captures screens, runs IA/responsive design reviews and N persona "
            "sessions (real browser, real seeded instance), then synthesizes an "
            "implementation-ready spec + findings.json. Observed findings are "
            "real and reproducible; persona reactions/verdicts are simulation "
            "(hypotheses, not user testimony). Pauses at a human approval gate "
            "by default (on_human_gate='stop') -- that is the expected, "
            "successful way an unattended round ends, not a failure. Can take "
            "many minutes (real LLM + browser sessions)."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "config_path": {
                    "type": "string",
                    "description": (
                        "Path to a project.yaml "
                        "(see `amplifier-simulated-user-research init`)."
                    ),
                },
                "on_human_gate": {
                    "type": "string",
                    "enum": ["stop", "fail", "auto-approve"],
                    "description": (
                        "How to handle the approval gate (default: stop -- pause "
                        "at the gate, the normal ending for an unattended run; "
                        "'fail' is a deprecated alias for stop)."
                    ),
                },
            },
            "required": ["config_path"],
        }

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        from amplifier_simulated_user_research import RoundConfig, run_round

        config_path = input_data["config_path"]
        on_human_gate = input_data.get("on_human_gate", "stop")

        def _run() -> dict[str, Any]:
            config = RoundConfig.from_yaml(config_path)
            problems = config.validate()
            if problems:
                return {"ok": False, "problems": problems}
            result = run_round(config, on_human_gate=on_human_gate)
            return {
                "ok": result.status in {"completed", "gate_reached"},
                "run_id": result.run_id,
                "status": result.status,
                "exit_code": result.exit_code,
                "gate_reached": result.gate_reached,
                "artifacts": {k: str(v) for k, v in result.artifacts.items()},
                "logs_dir": str(result.logs_dir) if result.logs_dir else None,
                "rounds_ledger": str(result.rounds_path)
                if result.rounds_path
                else None,
            }

        try:
            payload = await asyncio.to_thread(_run)
        except (FileNotFoundError, ValueError, RuntimeError) as e:
            return ToolResult(success=False, output=f"run_research_round failed: {e}")

        return ToolResult(success=bool(payload.get("ok")), output=payload)


class ResearchDoctorTool:
    """Environment diagnostics for running a simulated-user-research round."""

    @property
    def name(self) -> str:
        return "research_doctor"

    @property
    def description(self) -> str:
        return (
            "Run environment diagnostics for the simulated-user-research "
            "pipeline: checks the attractor CLI, agent-browser, the LLM "
            "provider API key, and this repo's own pipeline/script files. "
            "Cheap and safe -- never runs the pipeline itself."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "config_path": {
                    "type": "string",
                    "description": (
                        "Optional path to a project.yaml for config-specific "
                        "checks (personas_dir contents, browser_bundle registration)."
                    ),
                },
            },
        }

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        from amplifier_simulated_user_research import RoundConfig
        from amplifier_simulated_user_research.doctor import doctor

        config_path = input_data.get("config_path")

        def _run() -> dict[str, Any]:
            config = RoundConfig.from_yaml(config_path) if config_path else None
            checks = doctor(config)
            return {
                "all_ok": all(c.ok for c in checks),
                "checks": [asdict(c) for c in checks],
            }

        try:
            payload = await asyncio.to_thread(_run)
        except (FileNotFoundError, ValueError) as e:
            return ToolResult(success=False, output=f"research_doctor failed: {e}")

        return ToolResult(success=bool(payload["all_ok"]), output=payload)


async def mount(
    coordinator: Any, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Mount both tools into the coordinator."""
    run_tool = RunResearchRoundTool()
    doctor_tool = ResearchDoctorTool()
    await coordinator.mount("tools", run_tool, name=run_tool.name)
    await coordinator.mount("tools", doctor_tool, name=doctor_tool.name)
    logger.info(
        "tool-simulated-user-research mounted: registered %r, %r",
        run_tool.name,
        doctor_tool.name,
    )
    return {
        "name": "tool-simulated-user-research",
        "version": "0.1.0",
        "provides": [run_tool.name, doctor_tool.name],
    }
