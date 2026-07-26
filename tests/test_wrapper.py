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
    tmp_path: Path,
    fake_amplifier_body: str,
    *,
    role: str = "capture",
    report_name: str = "capture-notes.md",
    extra_args: list[str] | None = None,
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
            str(output_dir / report_name),
            "--run-id",
            RUN_ID,
            "--timeout-s",
            "30",
            *(extra_args or []),
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


class TestReviewRole:
    """--role review (4th hardening cycle): golden tests for the fix that
    closed round 4's review_responsive narrate-and-die incident (3x
    outcome=success, no file written -- see the .dot header and
    run_browser_node.py's module docstring, "--role review WAS ADDED")."""

    def test_review_requires_focus_and_app_source_hint(self, tmp_path):
        proc = _run_wrapper(
            tmp_path,
            _banner_then(_payload(execution_trace=_good_trace())),
            role="review",
            report_name="review-ia.md",
        )

        assert proc.returncode == 1
        assert _last_stdout_line(proc).startswith("error:")
        assert not (tmp_path / "out" / "review-ia.md").exists()

    def test_review_ia_writes_report_with_session_scoped_browser_commands(
        self, tmp_path
    ):
        """The session's own trace must show --session review-ia scoped
        commands (per build_review_instruction's isolation contract) for
        the browser-ran proof counter to see them as real activity."""
        trace = [
            _trace_entry("agent-browser --session review-ia close"),
            _trace_entry("agent-browser --session review-ia open http://127.0.0.1:9"),
            _trace_entry(
                "agent-browser --session review-ia screenshot /tmp/out/screens/review-ia-session.png"
            ),
        ]
        proc = _run_wrapper(
            tmp_path,
            _banner_then(
                _payload(
                    execution_trace=trace,
                    response="## Summary\nreal review content",
                )
            ),
            role="review",
            report_name="review-ia.md",
            extra_args=[
                "--review-focus",
                "ia",
                "--app-source-hint",
                "/tmp/app/src",
            ],
        )

        assert proc.returncode == 0
        assert _last_stdout_line(proc).startswith("wrote:")
        report = (tmp_path / "out" / "review-ia.md").read_text(encoding="utf-8")
        assert report.endswith("## Summary\nreal review content")
        assert f"Run ID: {RUN_ID}" in report

        manifest = json.loads(
            (tmp_path / "out" / ".session-manifest-review-ia.json").read_text(
                encoding="utf-8"
            )
        )
        assert manifest["navigations"] == 1
        assert manifest["screenshots"] == 1

    def test_review_responsive_zero_navigations_is_refused(self, tmp_path):
        """Pins the exact incident this role fixes: a narrated-but-never-
        driven review must be refused, not written, even though the
        session itself reported status=success."""
        proc = _run_wrapper(
            tmp_path,
            _banner_then(
                _payload(
                    execution_trace=[],
                    response=(
                        "Now I have all the information I need to write the "
                        "review. Let me compile the complete findings:"
                    ),
                )
            ),
            role="review",
            report_name="review-responsive.md",
            extra_args=[
                "--review-focus",
                "responsive",
                "--app-source-hint",
                "/tmp/app/src",
            ],
        )

        assert proc.returncode == 1
        assert _last_stdout_line(proc).startswith("error:")
        assert not (tmp_path / "out" / "review-responsive.md").exists()

    def test_review_focus_invalid_choice_rejected(self, tmp_path):
        proc = _run_wrapper(
            tmp_path,
            _banner_then(_payload(execution_trace=_good_trace())),
            role="review",
            report_name="review-ia.md",
            extra_args=[
                "--review-focus",
                "bogus",
                "--app-source-hint",
                "/tmp/app/src",
            ],
        )

        assert proc.returncode == 2  # argparse usage error
        assert "invalid choice" in proc.stderr


class TestNamedSessionCaptureAndPersona:
    """6th hardening cycle: capture and persona now get their own dedicated
    named session, mirroring build_review_instruction's pattern, after
    round 7's fabricated CRITICAL (an unrelated concurrent process on a
    shared dev box drove agent-browser's unnamed default session and
    landed a navigation in capture/persona's own tab). See
    run_browser_node.py's module docstring, "NAMED SESSIONS FOR
    CAPTURE/PERSONA WAS ADDED", for the full incident."""

    # Matches the same subset build_review_instruction's own golden test
    # checks (open|close|set|screenshot) -- deliberately excludes
    # scrollintoview/get/eval, which live in the shared
    # _CLICK_DISCIPLINE_BLOCK using generic placeholder selectors and are
    # covered instead by the prose immediately following that block (see
    # each builder's own docstring/prompt text).
    _COMMAND_RE = re.compile(
        r"agent-browser\s+(?:--\S+(?:\s+\S+)?\s+)*" r"(?:open|close|set|screenshot)\b"
    )

    def test_capture_every_agent_browser_command_is_session_scoped(self):
        wrapper = _load_wrapper_module()
        instruction = wrapper.build_capture_instruction(
            target_url="http://x",
            api_key="key",
            output_dir="/tmp/out",
            existing_content=None,
        )
        agent_browser_lines = [
            line for line in instruction.splitlines() if self._COMMAND_RE.search(line)
        ]
        assert agent_browser_lines, "expected agent-browser commands in the prompt"
        for line in agent_browser_lines:
            assert "--session capture" in line, line
        # The prose is allowed to WARN against `close --all` exactly once
        # (e.g. "NEVER run `agent-browser close --all`") -- it must not
        # appear anywhere else (i.e. as an actual instruction to run it).
        occurrences = [
            m.start() for m in re.finditer(r"agent-browser close --all", instruction)
        ]
        assert len(occurrences) == 1, occurrences
        warning_context = instruction[max(0, occurrences[0] - 20) : occurrences[0]]
        assert "NEVER run" in warning_context, warning_context

    def test_persona_every_agent_browser_command_is_session_scoped(self):
        wrapper = _load_wrapper_module()
        instruction = wrapper.build_persona_instruction(
            target_url="http://x",
            api_key="key",
            output_dir="/tmp/out",
            persona_name="marisol",
            persona_brief="brief body",
            existing_content=None,
        )
        agent_browser_lines = [
            line for line in instruction.splitlines() if self._COMMAND_RE.search(line)
        ]
        assert agent_browser_lines, "expected agent-browser commands in the prompt"
        for line in agent_browser_lines:
            assert "--session persona-marisol" in line, line
        # Same "warn exactly once, never instruct" contract as capture's own
        # test above -- see that test's comment for the reasoning.
        occurrences = [
            m.start() for m in re.finditer(r"agent-browser close --all", instruction)
        ]
        assert len(occurrences) == 1, occurrences
        warning_context = instruction[max(0, occurrences[0] - 20) : occurrences[0]]
        assert "NEVER run" in warning_context, warning_context

    def test_persona_session_name_is_unique_per_persona(self):
        """Three personas running in the same round (this pipeline's own
        sequential persona chain, STAGE 4) must never share a session name
        with each other -- distinct enough to stay unique, human-legible
        in a log."""
        wrapper = _load_wrapper_module()
        names = set()
        for persona_name in ("marisol", "devon", "priya"):
            instruction = wrapper.build_persona_instruction(
                target_url="http://x",
                api_key="key",
                output_dir="/tmp/out",
                persona_name=persona_name,
                persona_brief="brief",
                existing_content=None,
            )
            assert f"--session persona-{persona_name}" in instruction
            names.add(f"persona-{persona_name}")
        assert len(names) == 3

    def test_capture_role_trace_with_named_session_counts_as_real_activity(
        self, tmp_path
    ):
        """End-to-end golden test: a session trace using the NEW
        `--session capture`-scoped commands must still be recognized as
        real browser activity by the wrapper's own subprocess path (not
        just the unit-level _count_browser_activity check)."""
        trace = [
            _trace_entry("agent-browser --session capture close"),
            _trace_entry("agent-browser --session capture open http://127.0.0.1:9"),
            _trace_entry(
                "agent-browser --session capture screenshot /tmp/out/screens/01.png"
            ),
        ]
        proc = _run_wrapper(
            tmp_path,
            _banner_then(
                _payload(
                    execution_trace=trace,
                    response="## Screens Captured\nreal content\n## Observed Issues\nnone",
                )
            ),
        )

        assert proc.returncode == 0
        assert _last_stdout_line(proc).startswith("wrote:")
        manifest = json.loads(_manifest_path(tmp_path).read_text(encoding="utf-8"))
        assert manifest["navigations"] == 1
        assert manifest["screenshots"] == 1

    def test_persona_role_trace_with_named_session_counts_as_real_activity(
        self, tmp_path
    ):
        trace = [
            _trace_entry("agent-browser --session persona-marisol close"),
            _trace_entry(
                "agent-browser --session persona-marisol open http://127.0.0.1:9"
            ),
            _trace_entry(
                "agent-browser --session persona-marisol screenshot "
                "/tmp/out/screens/persona-marisol-session.png"
            ),
        ]
        personas_dir = tmp_path / "personas"
        personas_dir.mkdir()
        (personas_dir / "marisol.md").write_text("brief body", encoding="utf-8")

        proc = _run_wrapper(
            tmp_path,
            _banner_then(
                _payload(
                    execution_trace=trace,
                    response="# marisol -- Session Report\nreal content",
                )
            ),
            role="persona",
            report_name="persona-marisol.md",
            extra_args=[
                "--persona-name",
                "marisol",
                "--personas-dir",
                str(personas_dir),
            ],
        )

        assert proc.returncode == 0
        assert _last_stdout_line(proc).startswith("wrote:")
        manifest = json.loads(
            (tmp_path / "out" / ".session-manifest-persona-marisol.json").read_text(
                encoding="utf-8"
            )
        )
        assert manifest["navigations"] == 1
        assert manifest["screenshots"] == 1


class TestBuildReviewInstruction:
    """Unit tests for build_review_instruction (importlib, read-only)."""

    def test_ia_and_responsive_focus_produce_distinct_prompts(self):
        wrapper = _load_wrapper_module()
        ia = wrapper.build_review_instruction(
            review_focus="ia",
            target_url="http://x",
            api_key="key",
            output_dir="/tmp/out",
            app_source_hint="/tmp/src",
            existing_content=None,
        )
        responsive = wrapper.build_review_instruction(
            review_focus="responsive",
            target_url="http://x",
            api_key="key",
            output_dir="/tmp/out",
            app_source_hint="/tmp/src",
            existing_content=None,
        )
        assert "INFORMATION ARCHITECTURE" in ia
        assert "RESPONSIVE & ADAPTIVE" in responsive
        assert ia != responsive

    def test_every_agent_browser_command_is_session_scoped(self):
        """Load-bearing: since parallel_reviews runs both reviews
        concurrently, EVERY agent-browser invocation in the instruction
        must carry --session <review_focus-scoped-name> and the
        instruction must never suggest a bare `close --all` (which would
        tear down the OTHER review's in-flight browser)."""
        wrapper = _load_wrapper_module()
        instruction = wrapper.build_review_instruction(
            review_focus="ia",
            target_url="http://x",
            api_key="key",
            output_dir="/tmp/out",
            app_source_hint="/tmp/src",
            existing_content=None,
        )
        # Match actual invocations (agent-browser followed by a subcommand
        # token), not prose that merely mentions "the agent-browser CLI".
        command_re = re.compile(
            r"agent-browser\s+(?:--\S+(?:\s+\S+)?\s+)*"
            r"(?:open|close|set|screenshot)\b"
        )
        agent_browser_lines = [
            line for line in instruction.splitlines() if command_re.search(line)
        ]
        assert agent_browser_lines, "expected agent-browser commands in the prompt"
        for line in agent_browser_lines:
            assert "--session review-ia" in line, line
        # The prose is allowed to WARN against `close --all` exactly once
        # (e.g. "NEVER run `agent-browser close --all`") -- it must not
        # appear anywhere else (i.e. as an actual instruction to run it).
        occurrences = [
            m.start() for m in re.finditer(r"agent-browser close --all", instruction)
        ]
        assert len(occurrences) == 1, occurrences
        warning_context = instruction[max(0, occurrences[0] - 20) : occurrences[0]]
        assert "NEVER run" in warning_context, warning_context


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

    def test_session_scoped_commands_are_still_counted(self):
        """Regression guard: --role review's build_review_instruction pins
        every agent-browser invocation to --session <name> (see its
        "NAMED SESSION" docstring note) so two concurrent review branches
        never fight over the shared default browser daemon. The counting
        regex must still recognize `open`/`screenshot` as the next token
        after a value-taking flag like `--session review-ia`, not just
        bare boolean flags -- otherwise every review-role session would
        silently undercount its own real navigations/screenshots and
        hard-stop after 3 retries even though the browser genuinely ran."""
        wrapper = _load_wrapper_module()
        trace = [
            _trace_entry("agent-browser --session review-ia close"),
            _trace_entry(
                "agent-browser --session review-ia open http://x && "
                "agent-browser --session review-ia screenshot a.png"
            ),
        ]
        activity = wrapper._count_browser_activity(trace)
        assert activity == {"navigations": 1, "screenshots": 1, "commands": 3}

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
