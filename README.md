# Simulated User Research

Run a user-research-style audit round against any web product **with zero humans in
the loop until the approval gate**: seed a scratch instance with lived-in mock data,
capture every screen, run parallel design reviews, put N distinct personas through
real first-run browser sessions, and synthesize everything into one
implementation-ready spec — gated by a human approve/revise checkpoint at the end.

First proven against a real product (2026-07-22): the manual dry-run of this exact
process found 3 HIGH-severity bugs, a privacy leak (CDN font requests in a
"local-only" product), and consent-copy overclaims — in one afternoon.

**What this is — and isn't.** This is an automated pre-flight product audit with
persona lenses, not a replacement for talking to users. What the round *observes* is
real and reproducible: dead taps, contradictory copy, measured waits, third-party
network calls — each carries repro steps a human can re-run. What the personas *feel
and conclude* is simulation: one model role-playing three briefs. Their reactions and
verdicts are hypotheses to test with real users, not testimony from them. Used
honestly, it makes your first real user session worth running — it does not replace
it. What makes the findings worth triaging at all: a real browser against a real
seeded instance, producing reproducible, evidence-labeled findings — not a model
imagining screens.

## How it works

**The `.dot` file is the logic home**
([pipelines/simulated-user-research.dot](pipelines/simulated-user-research.dot)).
Stages, prompts, file contracts, retry policy, and the revision loop all live there —
change the process by editing the graph, not by re-implementing it elsewhere. Every
other surface in this repo (the Python lib, the CLI, the agent tools) is a **thin
adapter** that builds and runs `attractor` invocations of that same graph — none of
them re-implement pipeline logic. See the `amplifier-tool-leverage-patterns` skill for
the pattern this repo follows.

```
seed (script, resumable)
  → visual capture (browser agent, 2 viewports)
  → [ IA review ‖ responsive review ]      (parallel fan-out/fan-in)
  → persona 1 → persona 2 → persona 3      (STRICTLY sequential — one browser daemon)
  → synthesis → human gate (approve / revise ≤3 / end)
```

- **Files are the contract**: every stage reads/writes real artifacts under
  `output_dir` (`capture-notes.md`, `review-*.md`, `persona-*.md`, `research-spec.md`,
  `findings.json`).
- **Resumable**: every stage has a file-existence guard — re-running after a crash
  skips completed stages; delete an artifact to force that stage to re-run.
- **Browser bridge**: the capture and persona stages shell out to
  `scripts/run_browser_node.py`, a single-shot `amplifier run --mode single` wrapper
  that drives the [`agent-browser`](https://github.com/vercel-labs/agent-browser) CLI
  (must be on PATH) and captures the session's final response text as the deliverable
  artifact. See that script's module docstring for why (a documented loop-agent
  limitation with persona role-play, not a design choice you should second-guess).

## The four ways to use this

| Level | Surface | Consumer | Entry point |
|---|---|---|---|
| **L1** | `.dot` attractor pipeline | Other pipelines / the Amplifier Resolve dot-graph resolver | `pipelines/simulated-user-research.dot` + `pipelines/simulated-user-research.resolver.yaml` |
| **L2** | Python lib | Other codebases (`import amplifier_simulated_user_research`) | `amplifier_simulated_user_research/` |
| **L3** | Amplifier tool module | Agents (`run_research_round`, `research_doctor` tools) | `bundle.md` + `modules/tool-simulated-user-research/` |
| **L4** | CLI | Humans / scripts | `asur` / `amplifier-simulated-user-research` console scripts |

All four are thin adapters over the same `.dot` graph — the DRY rule this repo
follows throughout.

### L1 — the `.dot` pipeline directly

Run it exactly as proven manually, with the `attractor` CLI from
[amplifier-bundle-attractor](https://github.com/microsoft/amplifier-bundle-attractor)'s
`pipeline-runner` module:

```bash
attractor run pipelines/simulated-user-research.dot \
  --cwd  <dir your seed_command runs from> \
  --param target_url=http://host:port \
  --param api_key=<key personas use to log in> \
  --param seed_command="<shell command that seeds the scratch instance>" \
  --param personas_dir=/abs/path/to/personas \
  --param output_dir=/abs/path/for/artifacts \
  --param app_source_hint="/abs/path(s) the design reviewers should read" \
  --param persona1=marisol --param persona2=dev --param persona3=ken \
  --param sur_repo_dir=/abs/path/to/this/repo \
  --param browser_bundle=sur-browser-node \
  --param run_id=r-20260723-120000 \
  --provider anthropic \
  --logs-root /abs/path/for/artifacts/.attractor-logs \
  --on-human-gate fail
```

`--on-human-gate fail` is the raw engine's flag name for what the CLI/lib call
`stop`: the pipeline deliberately pauses at the gate once `research-spec.md` exists —
the normal ending for an unattended run. A human reads the spec, then re-runs the
same command in a terminal to answer the gate interactively (approve / request
revision). `run_id` is this run's identity (`r-YYYYMMDD-HHMMSS`); the L2/L4 layers
generate it automatically — a direct `attractor run` must pass it explicitly.

`pipelines/simulated-user-research.resolver.yaml` is the sidecar manifest that
registers this pipeline with the Amplifier Resolve dot-graph resolver's picker UI
(see the `amplifier-bundle-resolve` `RESOLVER_GUIDE.md`) — it declares the same
params as a typed form. It cannot express the two environment prerequisites (a
provider API key and `agent-browser` on the worker's PATH); see "Prerequisites"
below and the honesty note inside the file itself.

### L2 — the Python lib

```python
from amplifier_simulated_user_research import RoundConfig, run_round, doctor

config = RoundConfig.from_yaml("project.yaml")
for check in doctor(config):
    print(check.ok, check.name, check.detail)

result = run_round(config, on_human_gate="stop")
print(result.run_id, result.status, result.gate_reached, result.artifacts)
```

`RoundConfig` loads/validates a per-project YAML; `run_round` builds and executes the
`attractor run ...` invocation shown above, inspects the artifacts it produces, and
appends the run's record to the `rounds.jsonl` ledger; `doctor` runs read-only
environment diagnostics. The triage helpers (`load_findings`, `run_triage`,
`record_triage`, `precision_summary`) grade a run's findings and persist the verdicts
(see "Run identity, ledger, and triage"). None of these re-implement pipeline stages —
they orchestrate runs of `pipelines/simulated-user-research.dot`.

### L3 — agent-callable tools

`bundle.md` (repo root) wires `modules/tool-simulated-user-research/` onto a minimal
single-agent session (anthropic provider + loop-agent orchestrator), exposing two
tools:

- `research_doctor(config_path?)` — environment diagnostics; cheap and safe.
- `run_research_round(config_path, on_human_gate?)` — runs one round; can take many
  minutes (real LLM + browser sessions). Returns `status: "gate_reached"` on the
  normal, successful unattended-run ending.

```bash
amplifier run -B file:///abs/path/to/amplifier-simulated-user-research/bundle.md \
  --mode single --output-format json \
  "Use the research_doctor tool and report its findings verbatim."
```

`context/simulated-user-research-awareness.md` is the awareness doc loaded into that
bundle's system prompt, telling a session when/how to use the tools (and that a
gate-reached result is success, not failure).

### L4 — the CLI

```bash
asur init --dir my-round/                 # scaffold project.yaml + personas/ (incl. _TEMPLATE.md)
# edit my-round/project.yaml and rewrite my-round/personas/*.md for YOUR product
asur doctor --config my-round/project.yaml
asur run --config my-round/project.yaml   # pauses at the approval gate (--on-human-gate stop)
# read research-spec.md, answer the gate, then grade the findings:
asur triage --config my-round/project.yaml
```

`asur` is a short alias for the `amplifier-simulated-user-research` console script
(both installed by this package). `asur run` exits `0` for both `"completed"` and
`"gate_reached"` — the gate is the normal, successful ending for an unattended round.
`--on-human-gate stop` is the default and the documented choice (`fail` is kept as a
deprecated alias; the underlying engine flag is still named `fail`).

## Answering the gate

An unattended run (`--on-human-gate stop`, the default) deliberately pauses once
`research-spec.md` exists. To answer the gate:

```bash
# 1. read the spec the run pointed you at, then in a real terminal:
asur run --config my-round/project.yaml --on-human-gate console
# 2. the file guards skip every completed stage, so this goes straight to the
#    gate; answer interactively (approve / request revision / end)
asur triage --config my-round/project.yaml   # 3. then grade the findings
```

`console` requires an engine with console-gate support — on
`amplifier-bundle-attractor` `@main` since PR #95 (which this package pins, so a
normal install has it). On an older engine, `asur` fails loud with the exact
remediation instead of a traceback. In console mode the runner inherits your
terminal (no output capture),
so the ledger record is derived from ground truth (exit code, artifacts on disk,
engine logs) and `stdout_tail`/`attractor_status` are empty for that run.

## Run identity, ledger, and triage

Every run gets a **run_id** (`r-YYYYMMDD-HHMMSS`), passed into the graph as
`--param run_id` (stamped into its artifacts) and recorded in the ledger. Three
files carry the instrumentation:

- **`<output_dir>/rounds.jsonl`** — one record appended per run: `run_id`,
  `ts_start`/`ts_end`, `status`, `gate_reached`, `artifacts`, `wall_clock_s`,
  `per_stage_wall_clock` (mined from the engine's own per-node `status.json`
  `duration_ms` in that run's logs dir — `null` when nothing is derivable, never a
  guess), `prior_run_id` (the previous line's run_id — round N can re-check round
  N−1's P1s), and `gate`/`triage` (null until triage). The ledger lives in
  `output_dir` so it travels with the artifacts it describes.
- **`<output_dir>/findings.json`** — emitted by the synthesis stage (the graph's
  contract): `{"run_id", "findings": [{"id", "title", "severity", "evidence_tier",
  "confirmation", "repro", "sources"}]}` — stable finding IDs, evidence-tier labels.
- **`asur triage --config project.yaml`** — after you answer the gate: grades each
  finding **real / noise / wont-fix** (~30 seconds), records the gate verdict, and
  persists both into that run's `rounds.jsonl` record. It then reports the
  precision-at-gate line, with observed-tier and simulated-tier precision reported
  separately (persona simulation must not borrow the credibility of machine-checked
  observations).

**The three metrics this instrumentation exists to answer** (in priority order):

1. **Precision at gate** — % of findings a human graded real, per evidence tier.
2. **Fix-conversion** — % of P1s actually fixed within 14 days (trace finding IDs
   from `findings.json` into your tracker/commits).
3. **Cost + wall-clock receipt per round** — `wall_clock_s` and
   `per_stage_wall_clock` in the ledger.

## Install

This repo is a local-only `uv` project today (not yet published — see
"Provenance"). From the repo root:

```bash
uv sync            # installs the lib + CLI (asur, amplifier-simulated-user-research)
uv run asur doctor  # sanity-check your environment
```

### Engine dependency

`amplifier_simulated_user_research` depends directly on
`amplifier-module-pipeline-runner` as a normal git-subdirectory dependency:

```toml
"amplifier-module-pipeline-runner @ git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/pipeline-runner"
```

This was **verified, not assumed**: `pipeline-runner`'s own `pyproject.toml` uses
`[tool.uv.sources]` to override its `amplifier-module-loop-pipeline` dependency to a
local sibling path (`../loop-pipeline`) for the attractor repo's own in-repo
development — but its `[project.dependencies]` entry for that same package is a
proper `git+...#subdirectory=...` URL, and `[tool.uv.sources]` overrides do not
propagate to external consumers (they only apply when `uv` treats that project as
the workspace root). A real `uv sync` against a scratch project confirmed the whole
chain (`pipeline-runner` → `loop-pipeline` → `unified-llm-client`) resolves and
installs cleanly via git subdirectories alone, and that the `attractor` console
script lands in the consuming project's own `.venv/bin/`. `run_round()` invokes that
console script directly (via `PATH`, falling back to the sibling of the current
Python interpreter) rather than importing `pipeline-runner`'s internals — keeping the
dependency behind its own CLI boundary, per the DRY rule's "don't fold a
dependency's private engine into your lib" guidance.

An escape hatch (`RoundConfig.attractor_checkout`) remains for local development
against an unmerged/local `amplifier-bundle-attractor` checkout: when set, the runner
shells out via `uv run --project <checkout>/modules/pipeline-runner attractor ...`
instead. Unset (the default) for normal use.

### Prerequisites

- **A provider API key** in the environment matching your `project.yaml`'s
  `provider` field (default `anthropic` → `ANTHROPIC_API_KEY`). This is the LLM
  provider's key, unrelated to your project's own `api_key`/`api_key_env` (the
  *target application's* login credential personas use).
- **[`agent-browser`](https://github.com/vercel-labs/agent-browser)** on PATH — the
  capture and persona stages drive it directly.
  - **ARM64 / custom browser**: agent-browser's managed Chrome-for-Testing channel
    ships **no Linux ARM64 builds** (`agent-browser install` exits 2 there with an
    apt suggestion) — the CLI looks healthy while every browser stage fails. Point
    it at your own binary instead: export
    `AGENT_BROWSER_EXECUTABLE_PATH=<system chromium, or Playwright's
    ~/.cache/ms-playwright/chromium_headless_shell-<ver>/chrome-linux/headless_shell>`
    and `AGENT_BROWSER_ARGS="--no-sandbox"` — or set the equivalent
    `browser_executable_path` / `browser_args` keys in `project.yaml` (`run_round`
    exports them into the pipeline environment, so the project carries the fix
    instead of your shell). `asur doctor` launch-tests the browser for real (`open
    about:blank` + close — note this closes any live agent-browser session, so
    don't run doctor mid-round) and suggests the newest Playwright headless_shell
    it finds on the box.
- **The browser-node bundle registered** once:
  ```bash
  amplifier bundle add file:///abs/path/to/amplifier-simulated-user-research/browser-node-agent.yaml \
    --name sur-browser-node
  ```
  (`asur doctor` checks this registration when given a `--config`.)

Run `asur doctor` (or `research_doctor` via the L3 tools) any time to check all of
the above plus the pipeline's own files.

## Per-project setup

```bash
asur init --dir my-round/
# 1. edit my-round/project.yaml:
#      target_url, seed_command, seed_cwd, output_dir, app_source_hint,
#      api_key or api_key_env
#    (validation rejects the REPLACE-ME placeholders until you do)
# 2. rewrite my-round/personas/*.md session tasks for YOUR product
#    (keep identity + temperament; start from personas/_TEMPLATE.md)
asur doctor --config my-round/project.yaml
asur run --config my-round/project.yaml
# read research-spec.md, answer the gate, then:
asur triage --config my-round/project.yaml
```

## Personas

`personas/` holds the shipped roster (also copied by `asur init`): each `<name>.md`
brief defines who the persona is, their temperament, session tasks, and what makes
them say yes/no. The pipeline's persona nodes supply the browser mechanics and the
report format — briefs carry only identity and intent.

| Persona | Lens |
|---|---|
| `marisol` | Non-technical operator — silent taps teach her to touch less; jargon = "not for me" |
| `dev` | Impatient power user — wastes his time twice and it's gone |
| `ken` | Privacy-adversarial evaluator — one provable lie disqualifies the product |

Personas are per-project inputs: point `personas_dir` at your own roster for other
products. The graph is static, so the roster size = the number of persona node
pairs in the `.dot` (copy a `check_personaN`/`personaN` pair to extend, and add a
matching field to `RoundConfig.personas`).

**Authoring personas** (see `personas/_TEMPLATE.md` for the annotated version):
the shipped briefs' session tasks name a *specific* product's surfaces (a
WhatsApp-triage product) — rewrite the tasks for your product, keep identity +
temperament. The load-bearing mechanism: ONE falsifiable temperament rule per
persona; **declare the yes/no bar in advance** (a verdict judged against a bar
invented afterwards can justify anything); write session tasks as numbered,
falsifiable probes; **tag scripted steers with `PROBE:`** in the brief so reports
can't launder them as spontaneous discoveries; and backstory motivates tasks but is
**never admissible as severity evidence** — severity is justified by the product's
own promise, not by invented biography. `asur doctor` warns when your briefs are
still byte-identical to the shipped roster (unchanged defaults against a different
product = findings will be fiction).

## Provenance

Designed and authored with the attractor bundle's `attractor-expert` agent; process
proven manually first, then encoded. See the header comments in
`pipelines/simulated-user-research.dot` for the design decisions (browser bridge,
sequential-persona guarantee, fidelity choices, retry policy) — they're
load-bearing. Refactored into the four-leverage-level shape (L1 `.dot` + resolver
sidecar, L2 lib, L3 agent tools, L4 CLI) per the `amplifier-tool-leverage-patterns`
skill; the `.dot` itself was not changed except for path-reference comments (it moved
into `pipelines/`) and remains the single source of truth for pipeline behavior.
