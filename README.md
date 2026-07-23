# Simulated User Research

Run a full user-research round against any web product **with zero humans**: seed a
scratch instance with lived-in mock data, capture every screen, run parallel design
reviews, put N distinct personas through real first-run browser sessions, and
synthesize everything into one implementation-ready spec — gated by a human
approve/revise checkpoint at the end.

First proven against a real product (2026-07-22): the manual dry-run of this exact
process found 3 HIGH-severity bugs, a privacy leak (CDN font requests in a
"local-only" product), and consent-copy overclaims — in one afternoon.

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
  `output_dir` (`capture-notes.md`, `review-*.md`, `persona-*.md`, `research-spec.md`).
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
  --provider anthropic \
  --logs-root /abs/path/for/artifacts/.attractor-logs \
  --on-human-gate fail
```

`--on-human-gate fail` is correct for unattended runs: the pipeline deliberately
stops at the gate once `research-spec.md` exists — a human reads the spec, then
re-runs the same command in a terminal to answer the gate interactively (approve /
request revision).

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

result = run_round(config, on_human_gate="fail")
print(result.status, result.gate_reached, result.artifacts)
```

`RoundConfig` loads/validates a per-project YAML; `run_round` builds and executes the
`attractor run ...` invocation shown above and inspects the artifacts it produces;
`doctor` runs read-only environment diagnostics. None of these re-implement pipeline
stages — they orchestrate runs of `pipelines/simulated-user-research.dot`.

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
asur init --dir my-round/                 # scaffold project.yaml + personas/
# edit my-round/project.yaml and my-round/personas/*.md
asur doctor --config my-round/project.yaml
asur run --config my-round/project.yaml --on-human-gate fail
```

`asur` is a short alias for the `amplifier-simulated-user-research` console script
(both installed by this package). `asur run` exits `0` for both `"completed"` and
`"gate_reached"` — the gate is success for an unattended round.

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
# edit my-round/project.yaml:
#   target_url, seed_command, seed_cwd, output_dir, app_source_hint,
#   api_key or api_key_env
# review/replace my-round/personas/*.md for your product
asur doctor --config my-round/project.yaml
asur run --config my-round/project.yaml
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

## Provenance

Designed and authored with the attractor bundle's `attractor-expert` agent; process
proven manually first, then encoded. See the header comments in
`pipelines/simulated-user-research.dot` for the design decisions (browser bridge,
sequential-persona guarantee, fidelity choices, retry policy) — they're
load-bearing. Refactored into the four-leverage-level shape (L1 `.dot` + resolver
sidecar, L2 lib, L3 agent tools, L4 CLI) per the `amplifier-tool-leverage-patterns`
skill; the `.dot` itself was not changed except for path-reference comments (it moved
into `pipelines/`) and remains the single source of truth for pipeline behavior.
