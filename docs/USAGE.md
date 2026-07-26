# Usage — the full reference

The README covers the quickstart; this document carries the depth: how the
pipeline works, the full config reference, the gate/ledger/triage flow, the
engine-dependency design, and how to extend the persona roster.

## How a round works

**The `.dot` file is the logic home**
([pipelines/simulated-user-research.dot](../pipelines/simulated-user-research.dot)).
Stages, prompts, file contracts, retry policy, and the revision loop all live
there — change the process by editing the graph, not by re-implementing it
elsewhere. Every other surface (the Python lib, the CLI, the agent tools) is a
thin adapter that builds and runs `attractor` invocations of that same graph.
See the `amplifier-tool-leverage-patterns` skill for the pattern.

```
seed (script, resumable)
  → visual capture (browser agent, 2 viewports)
  → [ IA review ‖ responsive review ]      (parallel fan-out/fan-in)
  → persona 1 → persona 2 → persona 3      (STRICTLY sequential — one browser daemon)
  → synthesis → human gate (approve / revise ≤3 / end)
```

- **Files are the contract**: every stage reads/writes real artifacts under
  `output_dir`. Nothing depends on LLM context carrying forward.
- **Resumable**: every stage has a file-existence guard — re-running after a
  crash skips completed stages; delete an artifact to force its stage to re-run.
- **Browser bridge**: the capture and persona stages shell out to
  `scripts/run_browser_node.py`, a single-shot `amplifier run` wrapper that
  drives the [`agent-browser`](https://github.com/vercel-labs/agent-browser)
  CLI and captures the session's final response text as the deliverable. It
  parses the session's execution trace and **refuses to write a report when
  zero real browser navigations occurred** — hallucinated sessions fail loud.
  See that script's module docstring for the full design rationale.

## project.yaml reference

Required keys (see `examples/attention-firewall.project.yaml` for an annotated
worked example; `amplifier-simulated-user-research init` scaffolds a starter):

| Key | Meaning |
|---|---|
| `target_url` | Base URL of the running scratch instance to audit |
| `api_key` / `api_key_env` | The TARGET APP's login credential (literal, or env var name) — exactly one |
| `seed_command` | Shell command that seeds the scratch instance (safely re-runnable) |
| `seed_cwd` | Directory the seed AND reset commands run from |
| `reset_command` | OPTIONAL. Shell command that resets your app's state, run BEFORE `seed_command` |
| `personas_dir` | Folder of `<name>.md` persona briefs |
| `personas` | Exactly 3 names, each matching a brief file |
| `output_dir` | Where all round artifacts land |
| `app_source_hint` | Path(s) the design reviewers read (your app's source) |
| `browser_bundle` | Bundle name for browser sessions (default `simulated-user-research-browser-node`) |
| `provider` | LLM provider (`anthropic` default; needs `ANTHROPIC_API_KEY`) |

**`reset_command` — start every round from a representative state.** A round
that reuses a long-lived test fixture without resetting it silently corrupts its
own findings: two consecutive real rounds reported "triage is broken" when the
truth was a test queue a human had worked through days earlier. If your app has
a reset script, name it here and the pipeline runs it before seeding — once per
round (guarded by a `.reset-complete` marker, so resuming a crashed round never
re-resets the database out from under artifacts already collected), from
`seed_cwd`, hard-stopping loudly if it fails. Most projects have no reset step:
omit the key entirely and the stage safely no-ops. There is deliberately no
separate `reset_cwd` — the engine takes one working directory per run.

Optional: `sur_repo_dir` (auto-detected from the installed package),
`logs_root`, `browser_executable_path` / `browser_args` (custom browser — see
ARM64 below), `attractor_checkout` (dev-only engine escape hatch).

`amplifier-simulated-user-research run` rejects any config still carrying a `REPLACE ME`/`REPLACE_ME`
placeholder, so an unedited scaffold fails loud instead of producing fiction.

## Answering the gate

An unattended run (`--on-human-gate stop`, the default) deliberately pauses
once `research-spec.md` exists — that is the normal, successful ending
(`amplifier-simulated-user-research run` exits 0 and says so). Then:

```bash
# read research-spec.md, then in a real terminal:
amplifier-simulated-user-research run --config my-round/project.yaml --on-human-gate console
# file guards skip completed stages -> straight to the gate; answer
# interactively (approve / request revision / end)
amplifier-simulated-user-research triage --config my-round/project.yaml
```

Gate policy values: `stop` (default; pause at the gate), `console` (answer
interactively; the subprocess inherits your terminal, so for that run the
ledger record is derived from ground truth — exit code, artifacts on disk,
engine logs — and `stdout_tail`/`attractor_status` are empty), `auto-approve`
(non-interactive; picks each gate's first choice), `fail` (deprecated alias
for `stop`). Console-gate support is on `amplifier-bundle-attractor@main`
(PR #95); on an older engine the CLI fails loud with the exact remediation.

## Run identity, ledger, and triage

Every run gets a **run_id** (`r-YYYYMMDD-HHMMSS`), passed into the graph as
`--param run_id` (stamped into artifacts mechanically) and recorded in the
ledger. Three files carry the instrumentation:

- **`<output_dir>/rounds.jsonl`** — one record per run: `run_id`,
  `ts_start`/`ts_end`, `status`, `gate_reached`, `artifacts`, `wall_clock_s`,
  `per_stage_wall_clock` (mined from the engine's own per-node `status.json`
  `duration_ms`; `null` when not derivable — never a guess), `prior_run_id`
  (so round N can re-check round N−1's P1s), `harness` (see below), and
  `gate`/`triage` (null until triage). Lives in `output_dir` so it travels with
  the artifacts.
- **`<output_dir>/findings.json`** — emitted by synthesis:
  `{"run_id", "findings": [{"id", "title", "severity", "evidence_tier",
  "confirmation", "repro", "sources"}]}`.
- **`harness`** — which build produced the round: `tool_version`,
  `wrapper_sha256` and `pipeline_sha256` (sha256[:12] of
  `scripts/run_browser_node.py` and the pipeline `.dot` — the two surfaces that
  carry every prompt shaping agent behavior), plus the resolved `engine_path` /
  `engine_source`. A finding cannot be read without it: one round's "control does
  nothing" finding was an artifact of a browser-click bug fixed in a later build,
  and only the harness fingerprint distinguishes the two. `triage` warns when the
  round you are grading came from a different build than the one installed now.
  Fields that cannot be derived truthfully are omitted rather than guessed, and
  records written before this feature simply lack the block.
- **`amplifier-simulated-user-research triage`** — grades each finding **real / noise / wont-fix**
  (~30 seconds), records the gate verdict, persists both into the run's
  ledger record, and reports precision-at-gate with observed-tier and
  simulated-tier precision separated (simulation must not borrow the
  credibility of machine-checked observations).

**The three metrics** this exists to answer: (1) precision at gate, per
evidence tier; (2) fix-conversion — % of P1s fixed within 14 days (trace
finding IDs into your tracker); (3) cost + wall-clock receipt per round.

## Engine dependency (design note)

This package depends on `amplifier-module-pipeline-runner` as a normal
git-subdirectory dependency
(`git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/pipeline-runner`)
— verified installable: the whole chain (pipeline-runner → loop-pipeline →
unified-llm-client) resolves via git subdirectories, and the `attractor`
console script lands in the consuming environment. `run_round()` invokes that
console script as a subprocess (PATH first, then the interpreter's sibling
directory) rather than importing engine internals — the dependency stays
behind its own CLI boundary. `RoundConfig.attractor_checkout` is a dev-only
escape hatch that shells `uv run --project <checkout>/modules/pipeline-runner
attractor ...` against a local engine checkout instead.

## ARM64 / custom browser

agent-browser's managed Chrome-for-Testing channel ships **no Linux ARM64
builds** (`agent-browser install` exits 2 there) — the CLI looks healthy while
every browser stage fails. Point it at your own binary: export
`AGENT_BROWSER_EXECUTABLE_PATH=<system chromium, or
~/.cache/ms-playwright/chromium_headless_shell-<ver>/chrome-linux/headless_shell>`
and `AGENT_BROWSER_ARGS="--no-sandbox"`, or set `browser_executable_path` /
`browser_args` in project.yaml (`run_round` exports them into the pipeline
environment). The `doctor` subcommand launch-tests the browser for real (`open
about:blank` + close — this closes any live agent-browser session, so don't
run doctor mid-round) and suggests the newest Playwright headless_shell found
on the box.

## Resolve resolver sidecar

`pipelines/simulated-user-research.resolver.yaml` registers the pipeline with
the Amplifier Resolve dot-graph resolver's picker UI (a typed form over the
same params). It cannot express the environment prerequisites (provider key,
agent-browser on the worker's PATH) — see the honesty note inside the file.

## Extending the persona roster

The graph is static: the roster size = the number of persona node pairs in
the `.dot`. To go beyond 3, copy a `check_personaN`/`personaN` pair in
`pipelines/simulated-user-research.dot`, renumber, splice into the sequential
chain, and extend `RoundConfig.personas` validation to match. Persona
mechanics (browser driving, report format, evidence discipline) live in the
pipeline's wrapper prompts — briefs carry only identity and intent.

## Provenance (long form)

Process proven manually against a real product first, then encoded; the
`.dot`'s header comments record the load-bearing design decisions (browser
bridge, sequential-persona guarantee, retry policy). Hardened through
engineering/product/design council reviews — the consolidated backlog and the
honesty framing live in [BEST-IN-CLASS-BACKLOG.md](BEST-IN-CLASS-BACKLOG.md).
Structured per the `amplifier-tool-leverage-patterns` skill: L1 `.dot` +
resolver sidecar, L2 lib, L3 tool module, L4 CLI — all thin adapters over the
one logic home.
