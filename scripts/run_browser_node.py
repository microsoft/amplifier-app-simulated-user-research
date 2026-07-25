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
its LAST action. Instead, run the persona/capture/review task as a
single-shot `amplifier run --mode single --output-format json-trace` call
and treat the session's raw final response TEXT as the deliverable --
captured here, programmatically, regardless of whether the model's last
turn was a tool call or plain prose. A text-only final reply is no longer
a failure mode; it's the intended, expected way this now finishes. This
mirrors the pattern that worked in the project's manual (non-pipeline)
research round: "the persona report returned as the response text, not
agent-side file writes." (--role review was added a full hardening cycle
later than capture/persona, once review_responsive was directly observed
dying the identical way in production -- see "--role review WAS ADDED"
below for that incident.)

HARDENING SPRINT (3rd council round) additions -- read before editing:

1. BROWSER-RAN PROOF (kills the hallucinated-persona class). A dead
   target URL, or a model that just narrates a plausible-looking session
   without ever touching the browser, used to be indistinguishable from a
   genuine walkthrough -- both produce an 11KB report that reads fine and
   passes a byte-floor check. `--output-format json-trace` (not plain
   `json`) gives this wrapper the session's actual tool-call trace
   (`execution_trace`): every bash invocation, its exact command string,
   and its real exit status, straight from the engine's own tool:pre /
   tool:post hooks -- not the model's self-report. `_count_browser_activity`
   below counts REAL, SUCCEEDED `agent-browser open`/`screenshot`
   invocations from that trace. If zero real navigations (or zero real
   screenshots) are found, this script REFUSES to write the report at
   all and exits 1 with a loud reason -- so the .dot's verify_<stage> gate
   sees a missing/stale artifact and drives its retry/hard-stop path,
   instead of accepting a plausible fiction. A `.session-manifest-<stem>.json`
   sidecar is always written (when a trace was obtained) recording the
   raw counts, for humans debugging a hard-stop.

   This mirrors the "have the wrapper verify a durable file exists and is
   fresh" fallback design named in the hardening brief, but goes one step
   stronger: json-trace lets us inspect the SESSION'S OWN tool-call
   ground truth directly, rather than only inferring browser activity
   indirectly from a screenshot file's mtime. Both mechanisms are used
   together here for defense in depth -- the persona/capture instructions
   below still require a screenshot to a known path (see
   `_screenshot_hint`), and validate_artifact.py separately verifies every
   screenshot filename a capture report cites actually exists on disk.

2. RUN ID / PROVENANCE STAMPING IS MECHANICAL, NOT PROMPTED. A model can
   forget (or garble) a "Run ID: ..." line if merely asked to include one
   in its final reply. `_stamp_header` below prepends the provenance
   banner and the literal `Run ID: <run_id>` / `Date: <date>` lines to
   the model's response text BEFORE it is written to the report path --
   deterministically, in this script, never left to model compliance.
   This is what makes validate_artifact.py's Run ID / staleness check a
   real contract instead of a suggestion: the value is always correct
   whenever this wrapper is what wrote the file at all.

3. EVIDENCE-TIER / EPISTEMIC-HONESTY PROMPTING (Gate 2, product of the
   design panel's review): the persona and capture instructions below
   enforce the [OBSERVED]/[SIMULATED] tagging convention, the
   "In-Character Reactions (simulated think-aloud)" rename (was "Verbatim
   Confusions"), the "Verdict (simulated, per this persona's stated bar)"
   rename, the `PROBE (scripted in brief):` tag for brief-scripted
   findings, and `HYPOTHESIS:` marking for any claim about how a REAL
   (non-simulated) user would react. These are prompt-level asks -- this
   script cannot mechanically verify persona reasoning quality -- but the
   REQUIRED SECTION HEADERS this prompt enforces are mechanically checked
   downstream by validate_artifact.py, so a persona report missing them
   fails the gate and retries rather than silently shipping.

This script is invoked from a `shape=parallelogram` (tool) node's
tool_command in simulated-user-research.dot. It does NOT replace the
existing verify_<stage> file-ground-truth quality gate downstream in the
.dot -- that gate now calls scripts/validate_artifact.py (content
contracts, not a byte floor; see that script's module docstring) and
remains the sole judge of whether the written artifact is good enough.
This script's job is: run the single-shot session, verify the browser
genuinely ran, capture the final response text with a mechanically
correct provenance/Run-ID header, and write it to the target report path
-- exactly the mechanical step that box nodes were depending on the MODEL
to remember to do as its last tool call, PLUS the ground-truth check a
model's own self-report could never provide.

EXIT CODE / STDOUT CONTRACT (tool nodes route on context.tool.last_line --
the LAST non-empty stdout line -- so this script never lets report prose
reach stdout):
  exit 0, last stdout line "wrote:<n>"   -- subprocess ran, JSON parsed,
                                             real browser activity was
                                             confirmed, <n> bytes written
                                             to the report path (n may be
                                             small; the .dot's
                                             verify_<stage> node is what
                                             judges quality/completeness
                                             via validate_artifact.py,
                                             not this script).
  exit 1, last stdout line "error:<msg>" -- genuine infra failure
                                             (amplifier CLI missing/
                                             crashed, subprocess timeout,
                                             unparseable JSON) OR zero
                                             real browser navigations/
                                             screenshots detected in the
                                             session trace (the
                                             hallucinated-session case) --
                                             no report is written in
                                             either case.

GOTCHA (verified live, 2026-07-23): `amplifier run --mode single
--output-format json-trace` still prints a leading "Bundle '<name>'
prepared successfully" line onto STDOUT before the JSON payload, on
EVERY invocation (not only a cold module cache). This script tolerates
that by locating the first "{" in stdout and using
json.JSONDecoder().raw_decode from there rather than json.loads(stdout)
directly. If you see "error:no JSON object in output" or
"error:unparseable JSON output" in the node's stderr log, re-check this
assumption against the installed amplifier CLI version first -- it may
have changed.

--role synthesis WAS CONSIDERED AND DELIBERATELY NOT BUILT: the
hardening brief offered a choice -- convert synthesis to this single-shot
wrapper, or keep it a box node and justify. Justification (recorded here
and mirrored in the .dot's synthesis node comment): synthesis's
narrate-and-die risk on a FAILED REVISION ("a failed revision silently
returns the stale spec") is now caught structurally by a hash-staleness
check in the .dot (snapshot research-spec.md's hash before a revision
pass, hard-stop if it didn't change after synthesis runs) -- this targets
the EXACT failure mode named, without giving up what a box node gets
that a subprocess wrapper would not: goal_gate+retry_target (spec 3.4),
reasoning_effort=high override, and critically the engine's automatic
consume-once injection of the human's freeform gate feedback
(`human.gate.text`) as a durable prior turn ahead of the prompt (see
backend.py's step 5) -- reproducing that plumbing across the subprocess
boundary would need to pass the human's feedback as a shell-escaped
--param and forfeits the "it's just a normal box node" simplicity for no
proven benefit today. If synthesis is EVER observed dying the loop-agent
text-only-reply death shape in practice (it has zero such incidents in
this pipeline's history, per the .dot's own header comment), add a
--role synthesis mode here following the same pattern as capture/persona
below -- the wrapper and overlay bundle already generalize for it.


--role review WAS ADDED (4th hardening cycle) once the justification for
leaving review_ia/review_responsive as box nodes EXPIRED: round 4 hit the
narrate-and-die shape on review_responsive THREE TIMES IN A ROW --
status.json recorded outcome=success with
notes="Plain text response: Now I have all the information I need to
write the review. Let me compile the complete findings:" and no
review-responsive.md was ever written, burning all 3 retries and
hard-stopping that branch (synthesis went on to honestly report it under
Missing inputs -- the epistemic-honesty layer worked exactly as designed,
but the review itself was still lost for the round). This is the
identical loop-agent death shape documented above for capture/persona: a
final reply that narrates an intention ("let me write the review now")
with no paired tool call ends the session, and no SessionConfig knob can
reject it. The fix is the same structural one: --role review below drives
the SAME single-shot subprocess pattern, and -- per the hardening brief --
these reviews now also DRIVE THE BROWSER LIVE (both viewports, navigating
the running app) rather than only reading static capture-notes.md/
screens -- so they get the SAME browser-ran proof manifest + zero-
navigation refusal as capture/persona, not a weakened variant of it.
`--review-focus {ia,responsive}` selects which of the two review prompts
(build_review_instruction) is used; the section-header contract each
writes is unchanged from the retired box-node prompts, so
validate_artifact.py's `review` type contract (>= 3 "## "-level headers)
needed no changes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC
from datetime import datetime


def _last_line_exit(message: str, *, ok: bool) -> None:
    """Print exactly one routing-safe line to stdout, then exit.

    All diagnostic detail goes to stderr; stdout gets ONLY this single
    short token so the pipeline's tool.last_line extraction (last
    non-empty stdout line) is never accidentally fed multi-KB report
    prose or a stray blank line.
    """
    print(message, file=sys.stdout, flush=True)
    sys.exit(0 if ok else 1)


# ---------------------------------------------------------------------------
# Browser-ran proof: parse json-trace's execution_trace for REAL, SUCCEEDED
# agent-browser invocations. This is ground truth from the engine's own
# tool:pre/tool:post hooks -- not the model's self-report -- so it is immune
# to the "plausible narrated fiction" failure class a dead target URL used
# to enable.
# ---------------------------------------------------------------------------

_AB_TOKEN_RE = re.compile(r"agent-browser\b")
# Tolerate flags that take a value (e.g. `--session review-ia`), not just
# bare/boolean flags: the old `(?:\s+--\S+)*` pattern matched only
# `--flag` tokens with no following value, so `agent-browser --session
# review-ia open ...` (added for the review role's per-branch session
# isolation -- see build_review_instruction's "NAMED SESSION" note) would
# NOT have matched `open` as the very next token and this browser-ran
# proof counter would have silently undercounted every review-role
# navigation/screenshot, hard-stopping every review after 3 retries. The
# lookahead-guarded `(?:\s+(?!--)\S+)?` consumes AT MOST one non-flag
# value token after a `--flag`, so `--session review-ia` is skipped as a
# unit while a bare boolean flag like `--headless` still matches too.
_AB_FLAG_RE = r"(?:--[\w-]+(?:\s+(?!--)\S+)?\s+)*"
_AB_OPEN_RE = re.compile(r"agent-browser\s+" + _AB_FLAG_RE + r"open\b")
_AB_SCREENSHOT_RE = re.compile(r"agent-browser\s+" + _AB_FLAG_RE + r"screenshot\b")


def _count_browser_activity(execution_trace: list[dict]) -> dict[str, int]:
    """Count real agent-browser invocations from a json-trace execution_trace.

    Each trace entry is one bash tool call (possibly a `&&`-chained shell
    one-liner containing several agent-browser subcommands -- the CLI's
    own docs recommend chaining). A chain's exit reflects whether EVERY
    subcommand in it succeeded (`&&` short-circuits on the first
    failure), so `commands` (raw subcommand count, regardless of outcome)
    is counted unconditionally, while `navigations`/`screenshots` (real,
    succeeded invocations) are only counted when the whole bash call
    reported success with returncode 0.
    """
    navigations = 0
    screenshots = 0
    commands = 0
    for entry in execution_trace:
        if entry.get("type") != "tool_call" or entry.get("tool") != "bash":
            continue
        arguments = entry.get("arguments") or {}
        command = arguments.get("command") or ""
        if "agent-browser" not in command:
            continue
        commands += len(_AB_TOKEN_RE.findall(command))

        result = entry.get("result") or {}
        output = result.get("output") or {}
        succeeded = bool(result.get("success")) and output.get("returncode", 1) == 0
        if succeeded:
            navigations += len(_AB_OPEN_RE.findall(command))
            screenshots += len(_AB_SCREENSHOT_RE.findall(command))

    return {
        "navigations": navigations,
        "screenshots": screenshots,
        "commands": commands,
    }


def _write_manifest(
    *,
    output_dir: str,
    report_path: str,
    activity: dict[str, int],
    session_id: str | None,
) -> str:
    """Write the `.session-manifest-<report-stem>.json` sidecar.

    Filename contract (fixed with the parallel lib/CLI build): the stem is
    the report path's basename without extension, e.g. a report path of
    `.../capture-notes.md` writes `.session-manifest-capture-notes.json`;
    `.../persona-marisol.md` writes `.session-manifest-persona-marisol.json`.
    """
    stem = os.path.splitext(os.path.basename(report_path))[0]
    manifest_path = os.path.join(output_dir, f".session-manifest-{stem}.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "navigations": activity["navigations"],
                "screenshots": activity["screenshots"],
                "commands": activity["commands"],
                "session_id": session_id,
            },
            f,
            indent=2,
        )
    return manifest_path


def _stamp_header(
    response_text: str, *, run_id: str, date_str: str, banner_subject: str
) -> str:
    """Mechanically prepend the provenance banner + Run ID + Date.

    Deliberately NOT left to the model: a model can forget or garble a
    "Run ID: ..." line, but this script never does. This is what turns
    validate_artifact.py's Run ID / staleness check from a polite request
    into a real, unforgeable contract for every report this wrapper
    writes.
    """
    banner = (
        f"> Simulated session: {banner_subject} drove a real browser against a "
        "real instance. Product behaviors below (bug tables, timings, network "
        "checks) were observed in-session and are reproducible. First-person "
        "reactions and the verdict are simulation -- hypotheses about this user "
        "class, not testimony.\n\n"
        f"Run ID: {run_id}\n"
        f"Date: {date_str}\n\n"
        "---\n\n"
    )
    return banner + response_text


def build_capture_instruction(
    *,
    target_url: str,
    api_key: str,
    output_dir: str,
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

    return f"""You are conducting the VISUAL CAPTURE stage of a simulated-user-research round for a web product.

How this works: you have real bash access (agent-browser CLI) and can browse the live app. Do the real work with tools as you go. Your FINAL reply -- however it ends, whether or not it happens to include a tool call -- is captured programmatically and used AS the deliverable artifact. You do not need to save it to a file yourself; just make sure your LAST reply contains the complete, final, substantive report in the exact format specified below (no placeholders, no "I'll write this next" -- the actual content).

EVIDENCE DISCIPLINE (this run is audited): every observation in this report is [OBSERVED] -- something you personally saw in a real browser against a real instance this session. There is no persona judgment in this stage, only direct observation. Do not write a partial screen list and label the rest "to be completed" or "(in progress)" -- an incomplete walkthrough is a failed session that will be rejected and retried, not a partial pass. If you are running low on turns, prioritize finishing EVERY screen at a lower level of detail over doing a few screens exhaustively and leaving the rest unwritten.

Setup:
- As your first action, run: agent-browser close --all   (clears any stale browser daemon from a prior crashed run; harmless if nothing was running)
- Target application: {target_url}
- If the app requires a login/API key to view content, use: {api_key}
{resume_block}
Task:
Walk the application screen-by-screen using the agent-browser CLI via bash (agent-browser open / snapshot -ic / click / fill / scroll / screenshot). Visit every primary screen or route a first-time user would encounter (onboarding, main list/feed, a detail view, settings, any consent/permission screens). For EACH screen, capture it at TWO viewport widths:
  1. Mobile: agent-browser set viewport 390 844
  2. Desktop: agent-browser set viewport 1280 900

Save every screenshot into {output_dir}/screens/ with descriptive, sequential filenames, e.g. screens/01-onboarding-mobile.png, screens/01-onboarding-desktop.png, screens/02-main-feed-mobile.png. Zero-pad the sequence number. Create {output_dir}/screens/ if it does not exist. Capture at least 4 distinct screens at both viewports (8+ files total) -- this is verified mechanically downstream, not optional.

While you go, keep a running list of anything that looks broken, confusing, or unfinished: layout breaks, overlapping elements, dead taps, missing loading states, text truncation, or anything a real user would notice as a bug. This is NOT a persona simulation -- just an honest, first-pass visual and functional walkthrough note. Every bug you note must reference the specific screenshot filename where it's visible -- cite it as `screens/<filename>` (the same relative path you saved it to, e.g. `screens/01-onboarding-mobile.png`), not a bare filename with no path -- this is how the report is checked against what's actually on disk.

When finished, run agent-browser close to end the browser session cleanly -- REQUIRED, later stages assume no browser daemon is left running.

Your FINAL reply must be the complete report with these exact sections (this text is what gets saved -- there is no other save step; a provenance banner and Run ID are added automatically, you do not need to write them):
  ## Screens Captured
  (numbered list, each with its screenshot filenames cited as `screens/<filename>`)
  ## Observed Issues
  (each: screen + screenshot filename cited as `screens/<filename>`, what's wrong, severity guess P1/P2/P3)

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

    screenshot_hint = f"{output_dir}/screens/persona-{persona_name}-session.png"

    return f"""You are conducting a PERSONA SESSION as part of a simulated-user-research round. You embody ONE persona doing a TRUE first-run of the product in a real browser -- this is NOT a review of screenshots, you must actually drive the app live via the agent-browser CLI.

How this works: you have real bash access (agent-browser CLI) and can browse the live app. Do the real work with tools as you go, fully in character. Your FINAL reply -- however it ends, whether or not it happens to include a tool call -- is captured programmatically and used AS the deliverable artifact. You do not need to save it to a file yourself; just make sure your LAST reply contains the complete, final, substantive friction-log report in the exact format specified below (no placeholders, no "let me write this now" -- the actual content, in character).

EVIDENCE DISCIPLINE (this run is audited -- read carefully, it changes how you tag your own findings):
- Your Bug Table is [OBSERVED]: every row must be a real behavior you personally witnessed this session in a real browser against a real instance, with a "Reproducible steps" entry a human could replay verbatim. Never put a feeling or a guess in the Bug Table.
- Your In-Character Reactions and Verdict sections are [SIMULATED]: your persona's judgment, not a real human's testimony. Keep this distinction sharp in your own writing -- don't blur an observed bug into a simulated reaction or vice versa.
- If your persona brief SCRIPTED a specific task for you to do this session (e.g. "trigger the learn-from-feedback feature", "ask one pointed trap question", "edit the rules directly") and you did that scripted thing and it produced a finding, tag that finding `PROBE (scripted in brief):` in your report. Reporting a scripted probe as if it were a spontaneous discovery is dishonest even when the underlying bug is real -- name it as what it is.
- Any severity you assign must be justified by the PRODUCT'S OWN stated promise or observed behavior (e.g. copy that claims X while the UI does Y) -- never by your persona's personal preferences or backstory alone.
- Any claim about how a REAL human user (not you, a simulated persona) would react to this product must be marked `HYPOTHESIS:` -- you are one LLM in one costume for one session, not a study of real users.
- As part of this session, take at least one screenshot via `agent-browser screenshot {screenshot_hint}` at the moment that most shaped your verdict -- this is a durable, independently-checkable proof that you drove a real browser this session, not just a chat transcript. This is verified mechanically downstream.

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

Your FINAL reply must be the complete friction log with these exact sections, in this exact order (this text is what gets saved -- there is no other save step; a provenance banner and Run ID are added automatically, you do not need to write them -- just start with your own H1):
  # <your persona's name> -- Session Report
  (H1 title line, then a line noting today's date in your own words is fine but not required -- the Run ID and date are stamped for you automatically)
  ## Persona
  (name, one-line description from the brief)
  ## Friction Log
  (chronological; each entry: what you tried, what happened, severity P1 blocking / P2 annoying / P3 minor. EVERY entry above P3 must have a matching row in your Bug Table below -- P1/P2 friction with no corresponding bug-table row is an inconsistent report.)
  ## In-Character Reactions (simulated think-aloud)
  ([SIMULATED] direct quotes of anything you -- in character -- would have said out loud in confusion or delight)
  ## Delights
  (anything that worked well)
  ## Bug Table
  ([OBSERVED] markdown table: | Screen | Issue | Severity | Reproducible steps | -- every row must be something you actually did and saw this session. If you truly found zero bugs, write a single explicit line "No bugs observed this session." instead of an empty table -- do not leave this section ambiguous between "found nothing" and "gave up".)
  ## One Change
  (the SINGLE change that would most improve your experience)
  ## Verdict (simulated, per this persona's stated bar)
  ([SIMULATED] would you adopt/continue using this product? yes/no/maybe, one paragraph why, judged strictly against the yes/no bar YOUR persona brief defines -- not a generic opinion, and not a HYPOTHESIS about other users unless explicitly marked as one)

The report must be substantive and complete -- a bare section skeleton with no real findings does not satisfy this task."""


_REVIEW_TASKS = {
    "ia": {
        "title": "an INFORMATION ARCHITECTURE & LAYOUT",
        "task": (
            "Evaluate the information architecture and layout of the product: "
            "navigation structure, hierarchy/grouping of content, discoverability "
            "of key actions, labeling/wording clarity, visual hierarchy (what "
            "draws the eye first vs what should), and whether the screen-to-screen "
            "flow makes sense for a first-time user. Cross-reference what you see "
            "live in the browser against the actual source to catch discrepancies "
            "between intended and shipped behavior."
        ),
        "sections": (
            "  ## Summary (2-3 sentences: overall IA health)\n"
            "  ## Navigation & Structure\n"
            "  ## Hierarchy & Discoverability\n"
            "  ## Labeling & Copy\n"
            "  ## Specific Issues Found (numbered; each with screen/file "
            "reference, description, severity P1/P2/P3)\n"
            "  ## Recommendations (prioritized, concrete)"
        ),
    },
    "responsive": {
        "title": "a RESPONSIVE & ADAPTIVE DESIGN",
        "task": (
            "Evaluate how well the product adapts across two viewport widths "
            "(mobile ~390px, desktop ~1280px): breakpoint correctness, touch "
            "target/hit-area sizing on mobile, text reflow and truncation, "
            "layout shifts or broken grids, whether desktop uses the extra space "
            "well (vs just a stretched mobile layout), and any input-related "
            "issues (e.g. font-size under 16px causing mobile zoom, hover-only "
            "affordances with no touch equivalent). Drive BOTH viewports live in "
            "the browser for every screen you review -- do not judge responsive "
            "behavior from memory or from a single viewport."
        ),
        "sections": (
            "  ## Summary (2-3 sentences: overall responsive health)\n"
            "  ## Breakpoint Behavior\n"
            "  ## Touch/Input Concerns\n"
            "  ## Layout Issues by Screen (numbered; each with screen/file "
            "reference, mobile vs desktop comparison, severity P1/P2/P3)\n"
            "  ## Recommendations (prioritized, concrete)"
        ),
    },
}


def build_review_instruction(
    *,
    review_focus: str,
    target_url: str,
    api_key: str,
    output_dir: str,
    app_source_hint: str,
    existing_content: str | None,
) -> str:
    """Build the single-shot instruction for a design review role.

    Unlike the retired box-node review prompts (which read ONLY static
    capture-notes.md/screens and explicitly disclaimed browser access),
    this single-shot version DRIVES THE BROWSER LIVE -- the same
    browser-ran proof manifest + zero-navigation refusal that gates
    capture/persona now gates these reviews too, closing the gap that let
    a narrated-but-never-written review pass as "success" three times in
    round 4 (see this module's docstring, "--role review WAS ADDED").
    Reading the app's source for cross-reference is still encouraged (via
    bash/filesystem tools, already in the overlay bundle), but it is no
    longer the ONLY input -- the review must show its work against the
    REAL running app, at both viewports, this session.

    NAMED SESSION, NOT THE SHARED DEFAULT (load-bearing, read before
    touching this): capture and persona (STAGE 2/4 in the .dot) are wired
    as a strictly sequential chain specifically so only one agent-browser
    daemon is ever in flight -- see the .dot's "SEQUENTIAL PERSONAS"
    header note. The two reviews are DIFFERENT: parallel_reviews
    (STAGE 3) runs review_ia and review_responsive CONCURRENTLY
    (shape=component, max_parallel=2). agent-browser's unnamed "default
    session" is a single shared daemon per machine -- two concurrent
    subprocesses both driving the default session would silently
    stomp on each other's tabs/viewport/navigation state. Every
    agent-browser invocation below is therefore pinned to
    `--session review-{review_focus}` (review-ia / review-responsive),
    giving each review branch its own isolated browser context (own
    cookies, tabs, viewport) per agent-browser's session-isolation model.
    Cleanup uses `agent-browser --session review-{review_focus} close`
    (session-scoped), NEVER `close --all` -- `close --all` tears down
    EVERY session on the machine, which would kill the OTHER review's
    in-flight browser out from under it if both happened to be mid-walkthrough
    at the same moment. This is the one respect in which the review
    instruction deliberately does NOT mirror the capture/persona
    instructions' `close --all` cleanup step.
    """
    task_spec = _REVIEW_TASKS[review_focus]
    session_name = f"review-{review_focus}"

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

    screenshot_hint = f"{output_dir}/screens/review-{review_focus}-session.png"

    return f"""You are conducting {task_spec["title"]} review as part of a simulated-user-research round. You have real bash access (agent-browser CLI) and MUST drive the live application yourself -- this is not a review of memory or assumption, it is a review of what the running app actually does right now.

How this works: do the real work with tools as you go. Your FINAL reply -- however it ends, whether or not it happens to include a tool call -- is captured programmatically and used AS the deliverable artifact. You do not need to save it to a file yourself; just make sure your LAST reply contains the complete, final, substantive review in the exact format specified below (no placeholders, no "let me write this now" -- the actual content).

EVIDENCE DISCIPLINE (this run is audited): every observation is [OBSERVED] -- something you personally saw in a real browser against a real instance this session. Do not write a placeholder like "(in progress)" or "to be completed" anywhere -- an incomplete review is a failed review that will be rejected and retried, not a partial pass.

CRITICAL -- ISOLATED BROWSER SESSION: this review runs IN PARALLEL with the OTHER design review, and both may be driving a real browser AT THE SAME TIME. You MUST prefix every single agent-browser command with `--session {session_name}` (e.g. `agent-browser --session {session_name} open ...`). NEVER run a bare `agent-browser` command with no `--session` flag, and NEVER run `agent-browser close --all` -- that closes every session on the machine, including the other review's in-flight browser. Use ONLY `agent-browser --session {session_name} close` to end your own session.

Setup:
- As your first action, run: agent-browser --session {session_name} close   (clears any stale state from a prior crashed attempt at THIS review; harmless if nothing was running; scoped to your own session only)
- Target application: {target_url}
- If the app requires a login/API key to view content, use: {api_key}
- Application source (read via bash/filesystem to cross-reference routing, component structure, and CSS/breakpoints against what you see live): {app_source_hint}
- Visit every primary screen a first-time user would encounter, at BOTH viewport widths for every screen you review:
    1. Mobile: agent-browser --session {session_name} set viewport 390 844
    2. Desktop: agent-browser --session {session_name} set viewport 1280 900
- Take at least one screenshot via `agent-browser --session {session_name} screenshot {screenshot_hint}` as durable proof you drove a real browser this session -- this is verified mechanically downstream.
{resume_block}
Task:
{task_spec["task"]}

When finished, run `agent-browser --session {session_name} close` to end YOUR browser session cleanly -- REQUIRED, but scoped to your own session only (never `close --all`).

Output:
Your FINAL reply must be the complete review with these exact sections (this text is what gets saved -- there is no other save step; a provenance banner and Run ID are added automatically, you do not need to write them):
{task_spec["sections"]}

The review must be substantive and complete before you finish -- a bare section skeleton with no real findings does not satisfy this task. Never write a placeholder like "(in progress)" or "to be completed" anywhere in the file -- an incomplete review is a failed review that will be rejected and retried, not a partial pass."""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--role", required=True, choices=["capture", "persona", "review"]
    )
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
        "--run-id",
        required=True,
        help="This round's run_id (e.g. r-20260723-140501) -- stamped into "
        "the artifact's Run ID header mechanically, never left to model "
        "compliance. See _stamp_header.",
    )
    parser.add_argument(
        "--persona-name", default="", help="Required when --role persona"
    )
    parser.add_argument(
        "--personas-dir", default="", help="Required when --role persona"
    )
    parser.add_argument(
        "--review-focus",
        default="",
        choices=["", "ia", "responsive"],
        help="Required when --role review: which review prompt to run.",
    )
    parser.add_argument(
        "--app-source-hint",
        default="",
        help="Required when --role review: comma-separated file/dir path(s) "
        "for the reviewer to cross-reference against the live app.",
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
        banner_subject = "an LLM conducting the visual capture pass"
    elif args.role == "review":
        if not args.review_focus or not args.app_source_hint:
            _last_line_exit(
                "error:review role requires --review-focus and --app-source-hint",
                ok=False,
            )
            return
        instruction = build_review_instruction(
            review_focus=args.review_focus,
            target_url=args.target_url,
            api_key=args.api_key,
            output_dir=args.output_dir,
            app_source_hint=args.app_source_hint,
            existing_content=existing_content,
        )
        banner_subject = f"an LLM conducting the {args.review_focus} design review pass"
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
        banner_subject = f"an LLM role-playing {args.persona_name}"

    cmd = [
        "amplifier",
        "run",
        "-B",
        args.bundle,
        "--mode",
        "single",
        "--output-format",
        "json-trace",
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
    # line onto STDOUT before the --output-format json-trace payload. A
    # naive json.loads(stdout) breaks on this every time. Find the first
    # '{' and decode from there with raw_decode so the leaked banner line
    # is tolerated unconditionally.
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

    # --- Browser-ran proof (see module docstring, item 1) ---------------
    execution_trace = data.get("execution_trace") or []
    activity = _count_browser_activity(execution_trace)
    manifest_path = _write_manifest(
        output_dir=args.output_dir,
        report_path=args.report_path,
        activity=activity,
        session_id=data.get("session_id"),
    )

    if activity["navigations"] == 0:
        print(
            "[run_browser_node] REFUSING to write "
            f"{args.report_path}: zero real agent-browser navigations "
            f"detected in the session trace ({activity['commands']} "
            "agent-browser command(s) attempted, "
            f"{activity['navigations']} succeeded navigation(s), "
            f"{activity['screenshots']} succeeded screenshot(s)). This "
            "session likely narrated a plausible-looking report without "
            f"ever driving a real browser. See {manifest_path} and the "
            "full execution_trace in this node's status.json/output.txt "
            "log for the raw command list.",
            file=sys.stderr,
        )
        _last_line_exit("error:zero real browser navigations", ok=False)
        return

    if activity["screenshots"] == 0:
        print(
            "[run_browser_node] REFUSING to write "
            f"{args.report_path}: zero real agent-browser screenshots "
            f"detected in the session trace ({activity['navigations']} "
            "succeeded navigation(s) but 0 succeeded screenshots). Both "
            "capture and persona instructions require at least one real "
            f"screenshot as durable proof of a real session. See "
            f"{manifest_path} for the raw counts.",
            file=sys.stderr,
        )
        _last_line_exit("error:zero screenshots captured", ok=False)
        return

    response_text = data.get("response") or ""
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    stamped_text = _stamp_header(
        response_text,
        run_id=args.run_id,
        date_str=date_str,
        banner_subject=banner_subject,
    )

    with open(args.report_path, "w", encoding="utf-8") as f:
        f.write(stamped_text)

    print(
        f"[run_browser_node] wrote {len(stamped_text)} bytes to {args.report_path} "
        f"(session_id={data.get('session_id')}, navigations={activity['navigations']}, "
        f"screenshots={activity['screenshots']}, commands={activity['commands']})",
        file=sys.stderr,
    )
    _last_line_exit(f"wrote:{len(stamped_text)}", ok=True)


if __name__ == "__main__":
    main()
