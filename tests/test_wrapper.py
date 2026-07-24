"""Golden tests for scripts/run_browser_node.py (the single-shot wrapper).

The wrapper is owned by the pipeline side (READ-ONLY from this suite -- we
import/invoke it, never edit it). Pinned contract (post-hardening-sprint):

- Invokes `amplifier run --mode single --output-format json-trace` and
  tolerates the "Bundle '<name>' prepared successfully" banner leaked onto
  stdout before the JSON payload (verified live 2026-07-23).
- Parses `execution_trace` (engine tool:pre/tool:post ground truth) and
  counts REAL, SUCCEEDED `agent-browser open`/`screenshot` invocations.
- REFUSES to write the report on zero real navigations or zero real
  screenshots for browser roles (exit 1, loud stderr, NO report file) --
  the hallucinated-session kill switch.
- Always writes a `.session-manifest-<report-stem>.json` sidecar when a
  trace was obtained (even on refusal), recording the raw counts.
- Mechanically stamps a provenance banner + `Run ID: <run_id>` header onto
  the report (--run-id is a required argument).
- stdout stays a single routing-safe line (`wrote:<n>` / `error:<msg>`).

Tests run the wrapper as a subprocess against a fake `amplifier`
executable -- exactly the boundary the .dot's tool_command uses -- plus
importlib unit tests for the pure functions.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
WRAPPER_PATH = _REPO_ROOT / "scripts" / "run_browser_node.py"

RUN_ID = "r-20260723-120000"


def _load_wrapper_module():
    # dont_write_bytecode: keep the read-only scripts/ dir free of __pycache__
    original = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location("run_browser_node", WRAPPER_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = original


def _trace_entry(command: str, *, success: bool = True, returncode: int = 0) -> dict:
    """One json-trace execution_trace entry, shaped like the engine emits."""
    return {
        "type": "tool_call",
        "tool": "bash",
        "arguments": {"command": command},
        "result": {"success": success, "output": {"returncode": returncode}},
    }


def _good_trace() -> list[dict]:
    """A trace proving a real browser session: navigation + screenshot."""
    return [
        _trace_entry("agent-browser close --all"),
        _trace_entry("agent-browser open http://127.0.0.1:9"),
        _trace_entry("agent-browser screenshot /tmp/out/screens/01.png"),
    ]


def _payload(
    *,
    status: str = "success",
    response: str = "## Screens Captured\nreal content",
    execution_trace: list[dict] | None = None,
    session_id: str = "s-1",
) -> str:
    return json.dumps(
        {
            "status": status,
            "response": response,
            "session_id": session_id,
            "execution_trace": execution_trace if execution_trace is not None else [],
        }
    )


def _make_fake_amplifier(bin_dir: Path, script_body: str) -> None:
    """Install a fake `amplifier` executable into bin_dir."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe = bin_dir / "amplifier"
    exe.write_text(f"#!/bin/sh\n{script_body}\n", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _banner_then(payload: str) -> str:
    """Fake-amplifier body: leaked banner line, then the JSON payload."""
    return (
        f"echo \"Bundle 'fake-bundle' prepared successfully\"\n"
        f"cat << 'JSON_EOF'\n{payload}\nJSON_EOF"
    )


def _run_wrapper(
    tmp_path: Path, fake_amplifier_body: str, *, role: str = "capture"
) -> subprocess.CompletedProcess:
    """Run the wrapper against a fake `amplifier` on PATH."""
    bin_dir = tmp_path / "fakebin"
    _make_fake_amplifier(bin_dir, fake_amplifier_body)
    output_dir = tmp_path / "out"
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return subprocess.run(
        [
            sys.executable,
            str(WRAPPER_PATH),
            "--role",
            role,
            "--bundle",
            "fake-bundle",
            "--target-url",
            "http://127.0.0.1:9",
            "--api-key",
            "test-key",
            "--output-dir",
            str(output_dir),
            "--report-path",
            str(output_dir / "capture-notes.md"),
            "--run-id",
            RUN_ID,
            "--timeout-s",
            "30",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def _last_stdout_line(proc: subprocess.CompletedProcess) -> str:
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert lines, f"wrapper produced no stdout (stderr: {proc.stderr[-500:]})"
    return lines[-1]


def _manifest_path(tmp_path: Path) -> Path:
    return tmp_path / "out" / ".session-manifest-capture-notes.json"


class TestBannerTolerantJsonExtraction:
    def test_banner_before_json_is_tolerated(self, tmp_path):
        proc = _run_wrapper(
            tmp_path, _banner_then(_payload(execution_trace=_good_trace()))
        )

        assert proc.returncode == 0
        assert _last_stdout_line(proc).startswith("wrote:")
        report = (tmp_path / "out" / "capture-notes.md").read_text(encoding="utf-8")
        # response text preserved; provenance header stamped mechanically
        assert report.endswith("## Screens Captured\nreal content")
        assert f"Run ID: {RUN_ID}" in report
        assert report.startswith("> Simulated session:")

    def test_clean_json_without_banner_also_works(self, tmp_path):
        payload = _payload(execution_trace=_good_trace())
        proc = _run_wrapper(tmp_path, f"cat << 'JSON_EOF'\n{payload}\nJSON_EOF")

        assert proc.returncode == 0
        assert _last_stdout_line(proc).startswith("wrote:")

    def test_no_json_in_output_is_a_loud_error(self, tmp_path):
        proc = _run_wrapper(tmp_path, "echo 'no json here at all'")

        assert proc.returncode == 1
        assert _last_stdout_line(proc).startswith("error:")
        assert not (tmp_path / "out" / "capture-notes.md").exists()

    def test_non_success_session_status_is_an_error(self, tmp_path):
        proc = _run_wrapper(
            tmp_path,
            _banner_then(
                _payload(
                    status="error", response="partial", execution_trace=_good_trace()
                )
            ),
        )

        assert proc.returncode == 1
        assert _last_stdout_line(proc).startswith("error:")
        assert not (tmp_path / "out" / "capture-notes.md").exists()

    def test_amplifier_nonzero_exit_is_an_error(self, tmp_path):
        proc = _run_wrapper(tmp_path, "echo boom >&2\nexit 3")

        assert proc.returncode == 1
        assert _last_stdout_line(proc).startswith("error:")

    def test_stdout_is_single_routing_safe_line(self, tmp_path):
        """Tool nodes route on the LAST non-empty stdout line -- the wrapper
        must never leak report prose onto stdout."""
        proc = _run_wrapper(
            tmp_path,
            _banner_then(_payload(response="x" * 5000, execution_trace=_good_trace())),
        )

        assert proc.returncode == 0
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        assert len(lines) == 1
        # wrote:<n> where n is the STAMPED length (banner + Run ID + response)
        assert re.fullmatch(r"wrote:\d+", lines[0])
        assert int(lines[0].split(":")[1]) > 5000


class TestBrowserRanProof:
    def test_zero_navigations_refuses_to_write_report(self, tmp_path):
        """The hallucinated-session kill switch: a session that never drove
        the browser gets NO report file, exit 1, loud stderr."""
        trace = [
            _trace_entry("agent-browser open http://x:1", success=False, returncode=7),
            _trace_entry("echo unrelated"),
        ]
        proc = _run_wrapper(tmp_path, _banner_then(_payload(execution_trace=trace)))

        assert proc.returncode == 1
        assert _last_stdout_line(proc) == "error:zero real browser navigations"
        assert "REFUSING" in proc.stderr
        assert not (tmp_path / "out" / "capture-notes.md").exists()

    def test_empty_trace_also_refuses(self, tmp_path):
        proc = _run_wrapper(tmp_path, _banner_then(_payload(execution_trace=[])))

        assert proc.returncode == 1
        assert _last_stdout_line(proc) == "error:zero real browser navigations"
        assert not (tmp_path / "out" / "capture-notes.md").exists()

    def test_zero_screenshots_refuses_even_with_navigation(self, tmp_path):
        trace = [_trace_entry("agent-browser open http://127.0.0.1:9")]
        proc = _run_wrapper(tmp_path, _banner_then(_payload(execution_trace=trace)))

        assert proc.returncode == 1
        assert _last_stdout_line(proc) == "error:zero screenshots captured"
        assert "REFUSING" in proc.stderr
        assert not (tmp_path / "out" / "capture-notes.md").exists()

    def test_manifest_sidecar_written_on_success_with_correct_counts(self, tmp_path):
        trace = [
            _trace_entry("agent-browser close --all"),
            # chained one-liner: 2 subcommands in one succeeded bash call
            _trace_entry(
                "agent-browser open http://127.0.0.1:9 && "
                "agent-browser screenshot /tmp/out/screens/01.png"
            ),
            _trace_entry("agent-browser screenshot /tmp/out/screens/02.png"),
        ]
        proc = _run_wrapper(
            tmp_path, _banner_then(_payload(execution_trace=trace, session_id="s-42"))
        )

        assert proc.returncode == 0
        manifest = json.loads(_manifest_path(tmp_path).read_text(encoding="utf-8"))
        assert (
            manifest
            == {
                "navigations": 1,
                "screenshots": 2,
                "commands": 4,  # close --all (1) + chained open&&screenshot (2) + screenshot (1)
                "session_id": "s-42",
            }
        )

    def test_manifest_sidecar_written_even_on_refusal(self, tmp_path):
        """Refusal still records the raw counts for humans debugging a hard-stop."""
        trace = [
            _trace_entry("agent-browser open http://x:1", success=False, returncode=7)
        ]
        proc = _run_wrapper(tmp_path, _banner_then(_payload(execution_trace=trace)))

        assert proc.returncode == 1
        manifest = json.loads(_manifest_path(tmp_path).read_text(encoding="utf-8"))
        assert manifest["navigations"] == 0
        assert manifest["screenshots"] == 0
        assert manifest["commands"] == 1  # attempted, counted regardless of outcome

    def test_synthesis_role_not_built_is_rejected_loudly(self, tmp_path):
        """--role synthesis WAS CONSIDERED AND DELIBERATELY NOT BUILT (see the
        wrapper's module docstring: synthesis stays a box node -- goal_gate,
        reasoning_effort, and the engine's human.gate.text injection are worth
        more than wrapper uniformity; its revision-staleness risk is caught by
        a hash check in the .dot). This test pins that decision: the wrapper
        must reject the role at argument parsing, not half-support it -- so no
        browser-evidence requirement can ever misfire against synthesis."""
        proc = _run_wrapper(
            tmp_path,
            _banner_then(_payload(execution_trace=_good_trace())),
            role="synthesis",
        )

        assert proc.returncode == 2  # argparse usage error
        assert "invalid choice" in proc.stderr
        assert not (tmp_path / "out" / "capture-notes.md").exists()


class TestCountBrowserActivity:
    """Unit tests for the trace-counting arithmetic (importlib, read-only)."""

    def test_chained_command_counts_all_subcommands(self):
        wrapper = _load_wrapper_module()
        trace = [
            _trace_entry(
                "agent-browser open http://x && agent-browser screenshot a.png"
            )
        ]
        activity = wrapper._count_browser_activity(trace)
        assert activity == {"navigations": 1, "screenshots": 1, "commands": 2}

    def test_failed_call_counts_commands_but_not_success_metrics(self):
        wrapper = _load_wrapper_module()
        trace = [
            _trace_entry("agent-browser open http://x", success=True, returncode=1),
            _trace_entry("agent-browser screenshot a.png", success=False),
        ]
        activity = wrapper._count_browser_activity(trace)
        assert activity == {"navigations": 0, "screenshots": 0, "commands": 2}

    def test_non_bash_and_non_tool_call_entries_ignored(self):
        wrapper = _load_wrapper_module()
        trace = [
            {"type": "message", "content": "agent-browser open http://x"},
            {
                "type": "tool_call",
                "tool": "read_file",
                "arguments": {"command": "agent-browser open http://x"},
            },
        ]
        activity = wrapper._count_browser_activity(trace)
        assert activity == {"navigations": 0, "screenshots": 0, "commands": 0}

    def test_stamp_header_prepends_run_id_and_banner(self):
        wrapper = _load_wrapper_module()
        stamped = wrapper._stamp_header(
            "body text",
            run_id=RUN_ID,
            date_str="2026-07-23",
            banner_subject="an LLM role-playing marisol",
        )
        assert stamped.endswith("body text")
        assert f"Run ID: {RUN_ID}\n" in stamped
        assert "Date: 2026-07-23\n" in stamped
        assert "an LLM role-playing marisol" in stamped


class TestInstructionBuilders:
    def test_capture_instruction_includes_target_and_output(self):
        wrapper = _load_wrapper_module()
        instruction = wrapper.build_capture_instruction(
            target_url="http://x:1",
            api_key="k",
            output_dir="/tmp/out",
            existing_content=None,
        )
        assert "http://x:1" in instruction
        assert "/tmp/out" in instruction
        assert "PARTIAL DRAFT" not in instruction

    def test_capture_instruction_carries_resume_block(self):
        wrapper = _load_wrapper_module()
        instruction = wrapper.build_capture_instruction(
            target_url="http://x:1",
            api_key="k",
            output_dir="/tmp/out",
            existing_content="previous partial notes",
        )
        assert "PARTIAL DRAFT" in instruction
        assert "previous partial notes" in instruction

    def test_persona_instruction_embeds_brief(self):
        wrapper = _load_wrapper_module()
        instruction = wrapper.build_persona_instruction(
            target_url="http://x:1",
            api_key="k",
            output_dir="/tmp/out",
            persona_name="marisol",
            persona_brief="THE BRIEF BODY",
            existing_content=None,
        )
        assert "THE BRIEF BODY" in instruction
        assert "BEGIN PERSONA BRIEF" in instruction
