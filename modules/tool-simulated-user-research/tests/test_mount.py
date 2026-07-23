"""Protocol-compliance tests for the tool-simulated-user-research module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_module_tool_simulated_user_research import (
    ResearchDoctorTool,
    RunResearchRoundTool,
    mount,
)


@pytest.mark.asyncio
async def test_mount_registers_both_tools():
    """mount() must register both tools via coordinator.mount() (the Iron Law)."""
    coordinator = MagicMock()
    coordinator.mount = AsyncMock()

    result = await mount(coordinator)

    assert coordinator.mount.call_count == 2
    for call in coordinator.mount.call_args_list:
        assert call.args[0] == "tools"

    names = {call.args[1].name for call in coordinator.mount.call_args_list}
    assert names == {"run_research_round", "research_doctor"}

    assert result is not None
    assert result["name"] == "tool-simulated-user-research"
    assert set(result["provides"]) == names


@pytest.mark.asyncio
async def test_tools_have_required_properties():
    coordinator = MagicMock()
    coordinator.mount = AsyncMock()
    await mount(coordinator)

    for call in coordinator.mount.call_args_list:
        tool = call.args[1]
        assert isinstance(tool.name, str) and tool.name
        assert isinstance(tool.description, str) and tool.description
        assert isinstance(tool.input_schema, dict)
        assert callable(tool.execute)


@pytest.mark.asyncio
async def test_research_doctor_execute_runs_real_checks():
    """research_doctor is cheap and safe -- exercise it for real (no mocking)."""
    tool = ResearchDoctorTool()
    result = await tool.execute({})

    assert result.output is not None
    assert "all_ok" in result.output
    assert "checks" in result.output
    assert isinstance(result.output["checks"], list)
    assert len(result.output["checks"]) > 0
    for check in result.output["checks"]:
        assert "name" in check and "ok" in check and "detail" in check


@pytest.mark.asyncio
async def test_run_research_round_reports_missing_config_file():
    tool = RunResearchRoundTool()
    result = await tool.execute({"config_path": "/nonexistent/project.yaml"})

    assert result.success is False
    assert "run_research_round failed" in str(result.output)
