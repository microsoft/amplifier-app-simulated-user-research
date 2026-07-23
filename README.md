# Simulated User Research — an Attractor pipeline

Run a full user-research round against any web product **with zero humans**: seed a
scratch instance with lived-in mock data, capture every screen, run parallel design
reviews, put N distinct personas through real first-run browser sessions, and
synthesize everything into one implementation-ready spec — gated by a human
approve/revise checkpoint at the end.

First proven against a real product (2026-07-22): the manual dry-run of this exact
process found 3 HIGH-severity bugs, a privacy leak (CDN font requests in a
"local-only" product), and consent-copy overclaims — in one afternoon.

## How it works

**The `.dot` file is the logic home** ([simulated-user-research.dot](simulated-user-research.dot)).
Stages, prompts, file contracts, retry policy, and the revision loop all live there —
change the process by editing the graph, not by re-implementing it elsewhere.

```
seed (script, resumable)
  → visual capture (browser agent, 2 viewports)
  → [ IA review ∥ responsive review ]      (parallel fan-out/fan-in)
  → persona 1 → persona 2 → persona 3      (STRICTLY sequential — one browser daemon)
  → synthesis → human gate (approve / revise ≤3 / end)
```

- **Files are the contract**: every stage reads/writes real artifacts under
  `output_dir` (`capture-notes.md`, `review-*.md`, `persona-*.md`, `research-spec.md`).
- **Resumable**: every stage has a file-existence guard — re-running after a crash
  skips completed stages; delete an artifact to force that stage to re-run.
- **Browser bridge**: box nodes are full agent sessions with bash; they drive the
  [`agent-browser`](https://github.com/vercel-labs/agent-browser) CLI directly
  (must be on PATH).

## Running it

Requires the [amplifier-bundle-attractor](https://github.com/microsoft/amplifier-bundle-attractor)
checkout (the `pipeline-runner` module is the engine harness) and `ANTHROPIC_API_KEY` in env.

```bash
cd <amplifier-bundle-attractor>/modules/pipeline-runner

uv run attractor run /path/to/simulated-user-research.dot \
  --cwd  <dir your seed_command runs from> \
  --param target_url=http://host:port \
  --param api_key=<key personas use to log in> \
  --param seed_command="<shell command that seeds the scratch instance>" \
  --param personas_dir=/abs/path/to/personas \
  --param output_dir=/abs/path/for/artifacts \
  --param app_source_hint="/abs/path(s) the design reviewers should read" \
  --param persona1=marisol --param persona2=dev --param persona3=ken \
  --provider anthropic \
  --logs-root /abs/path/for/artifacts/.attractor-logs \
  --on-human-gate fail
```

`--on-human-gate fail` is correct for unattended runs: the pipeline deliberately
stops at the gate once `research-spec.md` exists — a human reads the spec, then
re-runs the same command in a terminal to answer the gate interactively (approve /
request revision).

## Personas

`personas/` holds the roster: each `<name>.md` brief defines who the persona is,
their temperament, session tasks, and what makes them say yes/no. The pipeline's
persona nodes supply the browser mechanics and the report format — briefs carry
only identity and intent. The shipped roster:

| Persona | Lens |
|---|---|
| `marisol` | Non-technical operator — silent taps teach her to touch less; jargon = "not for me" |
| `dev` | Impatient power user — wastes his time twice and it's gone |
| `ken` | Privacy-adversarial evaluator — one provable lie disqualifies the product |

Personas are per-project inputs: point `personas_dir` at your own roster for other
products. The graph is static, so the roster size = the number of persona node
pairs in the `.dot` (copy a `check_personaN`/`personaN` pair to extend).

## Provenance

Designed and authored with the attractor bundle's `attractor-expert` agent;
process proven manually first, then encoded. See the header comments in the
`.dot` for the design decisions (browser bridge, sequential-persona guarantee,
fidelity choices, retry policy) — they're load-bearing.
