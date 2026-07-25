# Simulated User Research

An automated product-audit tool. You point it at a running web app; it seeds
realistic data, walks every screen in a real browser, runs parallel design reviews,
puts N scripted personas through genuine first-run sessions, and synthesizes an
implementation-ready findings spec — with evidence tiers (OBSERVED findings carry
repro steps; SIMULATED persona judgment is labeled as such), a human approve/revise
gate, and a triage step that produces a measured precision number. One LLM + one
real browser + your real app: observed findings are reproducible facts, persona
verdicts are labeled simulation. It's the audit round you run **before** spending
real user-research participants — not a replacement for them.

First proven against a real product (2026-07-22): one afternoon found 3
HIGH-severity bugs, a privacy leak, and consent-copy overclaims.

## Quickstart

```bash
uv tool install git+https://github.com/microsoft/amplifier-app-simulated-user-research
amplifier-simulated-user-research doctor        # environment check (launches a real browser once)
amplifier-simulated-user-research init --dir my-round   # scaffold project.yaml + personas/
# edit my-round/project.yaml (target URL + credential, seed command) and
# rewrite my-round/personas/*.md session tasks for YOUR product
amplifier-simulated-user-research run --config my-round/project.yaml    # pauses at the approval gate
amplifier-simulated-user-research run --config my-round/project.yaml --on-human-gate console
amplifier-simulated-user-research triage --config my-round/project.yaml # grade findings -> precision number
```

The only required inputs: your app's URL + login credential, a shell command that
seeds it with realistic data, and persona briefs rewritten for your product.
Validation rejects the scaffold's REPLACE-ME placeholders until you edit them.

## Prerequisites

- **Python 3.11+** and [`uv`](https://docs.astral.sh/uv/).
- **[`agent-browser`](https://github.com/vercel-labs/agent-browser)** on PATH, then
  `agent-browser install`. *Linux ARM64*: no Chrome-for-Testing builds exist —
  set `AGENT_BROWSER_EXECUTABLE_PATH=<system chromium or Playwright's
  chromium_headless_shell>` and `AGENT_BROWSER_ARGS="--no-sandbox"` (or the
  `browser_executable_path`/`browser_args` keys in project.yaml). The `doctor`
  subcommand launch-tests this for real and suggests a binary if one is installed.
- **`ANTHROPIC_API_KEY`** in the environment (the pipeline's LLM provider key —
  unrelated to your app's own login credential in project.yaml).
- The pipeline engine (`amplifier-bundle-attractor`'s pipeline-runner) arrives
  automatically as a dependency, and the pipeline's data files (graph, scripts,
  personas) ship inside the wheel — no source checkout needed. Register the
  browser-session bundle once (the `doctor` subcommand prints the resolved yaml path):
  `amplifier bundle add file://<path>/bundles/browser-node-agent.yaml --name simulated-user-research-browser-node`.

## Four ways to consume it

**CLI** (primary; the `amplifier-simulated-user-research` command):

```bash
amplifier-simulated-user-research init --dir my-round
amplifier-simulated-user-research doctor --config my-round/project.yaml
amplifier-simulated-user-research run --config my-round/project.yaml
amplifier-simulated-user-research triage --config my-round/project.yaml
```

**Pure attractor pipeline** (composes into larger flows; the `.dot` is the logic
home — every other surface is a thin adapter over it):

```bash
attractor run pipelines/simulated-user-research.dot --provider anthropic \
  --on-human-gate fail --logs-root <dir> --cwd <seed_cwd> --param k=v ...
# required --params: target_url api_key seed_command personas_dir output_dir
#   app_source_hint persona1 persona2 persona3 sur_repo_dir browser_bundle run_id
```

**Python lib**:

```python
from amplifier_simulated_user_research import RoundConfig, run_round

config = RoundConfig.from_yaml("my-round/project.yaml")
result = run_round(config, on_human_gate="stop")
print(result.run_id, result.status, result.artifacts)
```

**Amplifier tool module** (agent-callable; `simulated-user-research.bundle.md` at repo root):

```bash
amplifier run -B file://<this-repo>/simulated-user-research.bundle.md --mode single \
  "Use the research_doctor tool and report its findings."
# tools: research_doctor (diagnostics), run_research_round (full round)
```

> The `file://` scheme is load-bearing — a bare path (`-B simulated-user-research.bundle.md`) is treated
> as a registered-bundle name and will not resolve.

## What a round produces

All under your `output_dir` — files are the contract; re-runs skip completed
stages (delete an artifact to force its stage to re-run):

- `capture-notes.md` — screen-by-screen walkthrough notes + `screens/*.png` at 2 viewports
- `review-ia.md` / `review-responsive.md` — parallel design reviews against your source
- `persona-<name>.md` — one friction log per persona, provenance-bannered
- `research-spec.md` — the synthesized, implementation-ready spec (gate attachment)
- `findings.json` — stable finding IDs with severity + evidence tier + repro
- `rounds.jsonl` — run ledger: run_id, timings, artifacts, prior-run linkage, triage

**The honesty model**: every finding is tiered — `[OBSERVED]` (machine-checkable,
carries repro steps) vs `[SIMULATED]` (persona judgment — a product of the brief,
not a human) — and persona reports open with a provenance banner saying exactly
that. The wrapper refuses to write a report when the session trace shows zero real
browser navigations (hallucinated sessions die loudly, not plausibly). The `triage`
subcommand grades findings real/noise/wont-fix and reports **precision at gate** with
observed and simulated tiers separated. Deep story: [docs/BEST-IN-CLASS-BACKLOG.md](docs/BEST-IN-CLASS-BACKLOG.md).

## Personas

`personas/` ships a 3-persona roster (non-technical operator, impatient power
user, privacy-adversarial evaluator) plus `_TEMPLATE.md`, which marks what's
[PORTABLE] (identity, temperament, the pre-declared yes/no bar) vs what you must
[REPLACE] (session tasks naming YOUR product's surfaces — unchanged defaults
against a different product produce fiction, and the `doctor` subcommand warns
about it).
Scripted steers are tagged `PROBE:` in briefs and reports, so scripted probes are
never laundered as spontaneous discoveries.

## More

`docs/USAGE.md` — full config reference, the gate/ledger/triage flow in detail,
the engine-dependency design, resolver-sidecar registration, and extending the
persona roster. `examples/attention-firewall.project.yaml` — the maintainers'
worked example, annotated.

## Provenance

Process proven manually against a real product first, then encoded into
`pipelines/simulated-user-research.dot` (the single logic home — see its header
comments for the load-bearing design decisions); hardened through engineering,
product, and design council reviews; built on
[amplifier-bundle-attractor](https://github.com/microsoft/amplifier-bundle-attractor).

## Contributing

> [!NOTE]
> This project is not currently accepting external contributions, but we're actively working toward opening this up. We value community input and look forward to collaborating in the future. For now, feel free to fork and experiment!

Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.
