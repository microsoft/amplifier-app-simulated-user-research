# SMOKE_TESTS.md

Read twice: at planning (design to these) and at verification (run them).
Gradient: 1 always; 2 when the `.dot` changes; 3 when pipeline mechanics change
(guards, validator, wrapper, prompts); 4 when browser nodes or orchestration change.

## 1. Doctor (~10s)

```bash
uv run amplifier-simulated-user-research doctor
```
Expect every check OK (on Linux ARM64 the two browser checks may FAIL — that is a
pass IF the remediation text names `AGENT_BROWSER_EXECUTABLE_PATH` and the
Playwright `headless_shell` fallback; a traceback is a failure).

## 2. Graph validation (~5s)

```bash
uv run python -c "
from amplifier_module_loop_pipeline import dot_parser, validation
g = dot_parser.parse_dot(open('pipelines/simulated-user-research.dot').read())
validation.validate_or_raise(g)
print(f'OK: {len(g.nodes)} nodes, {len(g.edges)} edges')"
```
Expect parse + structural validation clean. Node/edge counts go in the PR body.

## 3. Cheap resume smoke (~2 min, no browser, minimal LLM spend)

Point a valid project config's `output_dir` at any COMPLETED round's artifact
directory, then:

```bash
uv run amplifier-simulated-user-research run --config <project.yaml>
```
Expect: every stage's guard skips (artifact contracts satisfied in resume mode)
and the run reaches the human gate in ~1–2 minutes, printing the
"research spec ready" message. Any stage re-running = a guard or contract
regression. This is the highest-value-per-minute check in the repo.

## 4. Full live round (~1–2 h, real LLM spend + working browser)

```bash
uv run amplifier-simulated-user-research run --config <project.yaml>   # fresh output_dir
uv run amplifier-simulated-user-research run --config <project.yaml> --on-human-gate console
uv run amplifier-simulated-user-research triage --config <project.yaml>
```
Expect: full artifact set passing contracts (spec + findings.json + 3 persona
reports + 2 reviews + capture + screens/ + session manifests), gate answerable
(approve AND revise-with-feedback must both work), triage records verdicts and
prints the precision line, `rounds.jsonl` gains a linked record. Attach the
ledger line and artifact listing to the PR.
