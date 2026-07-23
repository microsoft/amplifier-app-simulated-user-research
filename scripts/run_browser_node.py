#!/usr/bin/env python3
"""run_browser_node.py -- single-shot amplifier wrapper for browser pipeline nodes.

WHY THIS EXISTS (read before editing):

loop-agent's coding-agent loop (amplifier-bundle-attractor/modules/loop-agent)
treats ANY assistant reply with no tool call as natural session completion:
if the reply doesn't end in "?" (AWAITING_INPUT heuristic), the session goes
straight to IDLE + session_end and process_input() RETURNS that text --
there is no SessionConfig knob to change this (checked: config.py's
SessionConfig has no "require tool call" / "auto-continue on text-only"
field, and the branch in agent_session.py.process_input is hardcoded, not
config-gated). Persona role-play makes models narrate ("I notice something
important as Marisol...") instead of pairing a final observation with a
tool call, and a system-prompt reminder not to do this decays over
attempts (proven: 3/3 identical deaths in this pipeline's own history).

STRUCTURAL FIX: stop asking the model to write the deliverable to disk as
its LAST action. Instead, run the persona/capture task as a single-shot
`amplifier run --mode single --output-format json` call and treat the
session's raw final response TEXT as the deliverable -- captured here,
programmatically, regardless of whether the model's last turn was a tool
call or plain prose. A text-only final reply is no longer a failure mode;
it's the intended, expected way this now finishes. This mirrors the
pattern that worked in the project's manual (non-pipeline) research round:
"the persona report returned as the response text, not agent-side file
writes."

This script is invoked from a `shape=parallelogram` (tool) node's
tool_command in simulated-user-research.dot. It does NOT replace the
existing verify_<stage> file-ground-truth quality gate downstream in the
.dot -- that gate (byte-size check + bounded retry + loud hard_stop) is
left completely unchanged and remains the sole judge of whether the
written artifact is good enough. This script's ONLY job is: run the
single-shot session, capture the final response text, and write it to the
target report path -- exactly the mechanical step that box nodes were
depending on the MODEL to remember to do as its last tool call.

EXIT CODE / STDOUT CONTRACT (tool nodes route on context.tool.last_line --
the LAST non-empty stdout line -- so this script never lets report prose
reach stdout):
  exit 0, last stdout line "wrote:<n>"   -- subprocess ran, JSON parsed,
                                             <n> bytes written to the
                                             report path (n may be small;
                                             the .dot's verify_<stage> node
                                             is what judges quality/size,
                                             not this script).
  exit 1, last stdout line "error:<msg>" -- genuine infra failure (amplifier
                                             CLI missing/crashed, subprocess
                                             timeout, unparseable JSON).
                                             This is NOT the "model
                                             narrated instead of writing a
                                             file" case -- that case now
                                             always exits 0, because the
                                             narration IS the deliverable.

GOTCHA (verified live, 2026-07-23): `amplifier run --mode single
--output-format json` still prints a leading "Bundle '<name>' prepared
successfully" line onto STDOUT before the JSON payload, on EVERY
invocation (not only a cold module cache). This script tolerates that by
locating the first "{" in stdout and using json.JSONDecoder().raw_decode
from there rather than json.loads(stdout) directly. If you see
"error:no JSON object in output" or "error:unparseable JSON output" in
the node's stderr log, re-check this assumption against the installed
amplifier CLI version first -- it may have changed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time


def _last_line_exit(message: str, *, ok: bool) -> None:
    """Print exactly one routing-safe line to stdout, then exit.

    All diagnostic detail goes to stderr; stdout gets ONLY this single
    short token so the pipeline's tool.last_line extraction (last
    non-empty stdout line) is never accidentally fed multi-KB report
    prose or a stray blank line.
    """
    print(message, file=sys.stdout, flush=True)
    sys.exit(0 if ok else 1)


def build_capture_instruction(
    *, target_url: str, api_key: str, output_dir: str, existing_content: str | None
) -> str:
    resume_block = ""
    if existing_content:
        resume_block = (
            "\nA PARTIAL DRAFT from a previous attempt already exists "
            f"({len(existing_content)} bytes). Treat it as a starting point -- "
            "continue and extend it, don't start over from scratch. "
            "Partial draft follows, between the markers:\n"
            "----- BEGIN PARTIAL DRAFT -----\n"
            f"{existing_content}\n"
            "----- END PARTIAL DRAFT -----\n"
        )

    return f"""You are conducting the VISUAL CAPTURE stage of a simulated-user-research round for a web product.

How this works: you have real bash access (agent-browser CLI) and can browse the live app. Do the real work with tools as you go. Your FINAL reply -- however it ends, whether or not it happens to include a tool call -- is captured programmatically and used AS the deliverable artifact. You do not need to save it to a file yourself; just make sure your LAST reply contains the complete, final, substantive report in the exact format specified below (no placeholders, no "I'll write this next" -- the actual content).

Setup:
- As your first action, run: agent-browser close --all   (clears any stale browser daemon from a prior crashed run; harmless if nothing was running)
- Target application: {target_url}
- If the app requires a login/API key to view content, use: {api_key}
{resume_block}
Task:
Walk the application screen-by-screen using the agent-browser CLI via bash (agent-browser open / snapshot -ic / click / fill / scroll / screenshot). Visit every primary screen or route a first-time user would encounter (onboarding, main list/feed, a detail view, settings, any consent/permission screens). For EACH screen, capture it at TWO viewport widths:
  1. Mobile: agent-browser set viewport 390 844
  2. Desktop: agent-browser set viewport 1280 900

Save every screenshot into {output_dir}/screens/ with descriptive, sequential filenames, e.g. screens/01-onboarding-mobile.png, screens/01-onboarding-desktop.png, screens/02-main-feed-mobile.png. Zero-pad the sequence number. Create {output_dir}/screens/ if it does not exist.

While you go, keep a running list of anything that looks broken, confusing, or unfinished: layout breaks, overlapping elements, dead taps, missing loading states, text truncation, or anything a real user would notice as a bug. This is NOT a persona simulation -- just an honest, first-pass visual and functional walkthrough note.

When finished, run agent-browser close to end the browser session cleanly -- REQUIRED, later stages assume no browser daemon is left running.

Your FINAL reply must be the complete report with these exact sections (this text is what gets saved -- there is no other save step):
  ## Screens Captured
  (numbered list, each with its screenshot filenames)
  ## Observed Issues
  (each: screen, what's wrong, severity guess P1/P2/P3)

The report must be substantive and complete -- a bare section skeleton with no real findings does not satisfy this task."""


def build_persona_instruction(
    *,
    target_url: str,
    api_key: str,
    output_dir: str,
    persona_name: str,
    persona_brief: str,
    existing_content: str | None,
) -> str:
    resume_block = ""
    if existing_content:
        resume_block = (
            "\nA PARTIAL DRAFT from a previous attempt already exists "
            f"({len(existing_content)} bytes). Treat it as a starting point -- "
            "continue and extend it, don't start over from scratch. "
            "Partial draft follows, between the markers:\n"
            "----- BEGIN PARTIAL DRAFT -----\n"
            f"{existing_content}\n"
            "----- END PARTIAL DRAFT -----\n"
        )

    return f"""You are conducting a PERSONA SESSION as part of a simulated-user-research round. You embody ONE persona doing a TRUE first-run of the product in a real browser -- this is NOT a review of screenshots, you must actually drive the app live via the agent-browser CLI.

How this works: you have real bash access (agent-browser CLI) and can browse the live app. Do the real work with tools as you go, fully in character. Your FINAL reply -- however it ends, whether or not it happens to include a tool call -- is captured programmatically and used AS the deliverable artifact. You do not need to save it to a file yourself; just make sure your LAST reply contains the complete, final, substantive friction-log report in the exact format specified below (no placeholders, no "let me write this now" -- the actual content, in character).

Your persona brief (this defines who you are, your goals, your technical comfort level, and what would make you say yes or no to this product -- adopt this persona fully for the rest of the session; do not break character in your actions or your written log):
----- BEGIN PERSONA BRIEF -----
{persona_brief}
----- END PERSONA BRIEF -----

Setup:
1. As your first action, run: agent-browser close --all  (clears any stale browser daemon; harmless if none running -- REQUIRED, since persona sessions run one at a time and must never overlap another session's browser state)
2. Open a brand-new, logged-out browser session (do not reuse any saved cookies/state): agent-browser open {target_url}
3. If the product requires an account/API key to proceed past onboarding, use: {api_key} -- exactly as your persona would encounter it (only when the product's own flow asks for it, not before, if your persona is doing a genuine first run).
{resume_block}
Task:
Act as this persona doing REAL first-run onboarding and the concrete tasks described in your brief. Read what's on screen the way a real person would -- don't just execute the minimum clicks to finish. Notice friction: confusing copy, unclear next steps, dead taps, missing feedback, anything that would make a real user hesitate, get lost, or quit. Also notice delights: anything that clearly worked well or exceeded expectations.

When finished, close your browser: agent-browser close

Your FINAL reply must be the complete friction log with these exact sections (this text is what gets saved -- there is no other save step):
  ## Persona
  (name, one-line description from the brief)
  ## Friction Log
  (chronological; each entry: what you tried, what happened, severity P1 blocking / P2 annoying / P3 minor)
  ## Verbatim Confusions
  (direct quotes of anything you -- in character -- would have said out loud in confusion)
  ## Delights
  (anything that worked well)
  ## One Change
  (the SINGLE change that would most improve your experience)
  ## Verdict
  (would you adopt/continue using this product? yes/no/maybe, one paragraph why)
  ## Bug Table
  (markdown table: | Screen | Issue | Severity | Reproducible steps |)

The report must be substantive and complete -- a bare section skeleton with no real findings does not satisfy this task."""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, choices=["capture", "persona"])
    parser.add_argument(
        "--bundle",
        required=True,
        help="Path or bundle ref for the overlay agent bundle (-B)",
    )
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--report-path",
        required=True,
        help="Absolute path to write the captured response text to",
    )
    parser.add_argument(
        "--persona-name", default="", help="Required when --role persona"
    )
    parser.add_argument(
        "--personas-dir", default="", help="Required when --role persona"
    )
    parser.add_argument(
        "--timeout-s", type=int, default=3900, help="Subprocess-level backstop timeout"
    )
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    existing_content: str | None = None
    if os.path.isfile(args.report_path):
        try:
            with open(args.report_path, "r", encoding="utf-8", errors="replace") as f:
                existing_content = f.read()
            if not existing_content.strip():
                existing_content = None
        except OSError:
            existing_content = None

    if args.role == "capture":
        instruction = build_capture_instruction(
            target_url=args.target_url,
            api_key=args.api_key,
            output_dir=args.output_dir,
            existing_content=existing_content,
        )
    else:
        if not args.persona_name or not args.personas_dir:
            _last_line_exit(
                "error:persona role requires --persona-name and --personas-dir",
                ok=False,
            )
            return
        brief_path = os.path.join(args.personas_dir, f"{args.persona_name}.md")
        try:
            with open(brief_path, "r", encoding="utf-8") as f:
                persona_brief = f.read()
        except OSError as e:
            _last_line_exit(
                f"error:could not read persona brief {brief_path}: {e}", ok=False
            )
            return
        instruction = build_persona_instruction(
            target_url=args.target_url,
            api_key=args.api_key,
            output_dir=args.output_dir,
            persona_name=args.persona_name,
            persona_brief=persona_brief,
            existing_content=existing_content,
        )

    cmd = [
        "amplifier",
        "run",
        "-B",
        args.bundle,
        "--mode",
        "single",
        "--output-format",
        "json",
        instruction,
    ]

    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=args.timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        print(
            f"[run_browser_node] amplifier run timed out after {elapsed:.0f}s "
            f"(--timeout-s={args.timeout_s})",
            file=sys.stderr,
        )
        _last_line_exit("error:subprocess timeout", ok=False)
        return
    except FileNotFoundError as e:
        print(f"[run_browser_node] amplifier CLI not found: {e}", file=sys.stderr)
        _last_line_exit("error:amplifier CLI not found", ok=False)
        return

    if proc.returncode != 0:
        print(
            f"[run_browser_node] amplifier run exited {proc.returncode}\n"
            f"--- stdout (last 2000 chars) ---\n{proc.stdout[-2000:]}\n"
            f"--- stderr (last 2000 chars) ---\n{proc.stderr[-2000:]}",
            file=sys.stderr,
        )
        _last_line_exit(f"error:amplifier run exited {proc.returncode}", ok=False)
        return

    # GOTCHA (confirmed live, every invocation -- not just cold-cache): the
    # amplifier CLI prints a leading "Bundle '<name>' prepared successfully"
    # line onto STDOUT before the --output-format json payload, even in
    # --mode single --output-format json mode. A naive json.loads(stdout)
    # breaks on this every time. Find the first '{' and decode from there
    # with raw_decode so the leaked banner line is tolerated unconditionally.
    brace_idx = proc.stdout.find("{")
    if brace_idx == -1:
        print(
            f"[run_browser_node] no JSON object found in stdout\n"
            f"--- raw stdout (last 2000 chars) ---\n{proc.stdout[-2000:]}",
            file=sys.stderr,
        )
        _last_line_exit("error:no JSON object in output", ok=False)
        return
    try:
        data, _ = json.JSONDecoder().raw_decode(proc.stdout[brace_idx:])
    except json.JSONDecodeError as e:
        print(
            f"[run_browser_node] could not parse JSON output: {e}\n"
            f"--- raw stdout (last 2000 chars) ---\n{proc.stdout[-2000:]}",
            file=sys.stderr,
        )
        _last_line_exit("error:unparseable JSON output", ok=False)
        return

    if data.get("status") != "success":
        print(
            f"[run_browser_node] session reported non-success status: {data}",
            file=sys.stderr,
        )
        _last_line_exit(f"error:session status {data.get('status')!r}", ok=False)
        return

    response_text = data.get("response") or ""

    with open(args.report_path, "w", encoding="utf-8") as f:
        f.write(response_text)

    print(
        f"[run_browser_node] wrote {len(response_text)} bytes to {args.report_path} "
        f"(session_id={data.get('session_id')})",
        file=sys.stderr,
    )
    _last_line_exit(f"wrote:{len(response_text)}", ok=True)


if __name__ == "__main__":
    main()
