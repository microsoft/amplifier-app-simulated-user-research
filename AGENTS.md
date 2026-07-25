# AGENTS.md — amplifier-app-simulated-user-research

Automated product-audit rounds: seed a scratch instance, drive real-browser persona
sessions + design reviews, synthesize an evidence-tiered findings spec behind a human
gate. Read `PRINCIPLES.md` before designing changes; `SMOKE_TESTS.md` before verifying.

## Naming (hard rules)

- The CLI command is **`amplifier-simulated-user-research`** — never abbreviate it.
  The retired four-letter acronym of this tool's name (a‑s‑u‑r) is **banned**
  everywhere: code, docs, messages, commits, PRs.
- The user-facing browser-node bundle name is `simulated-user-research-browser-node`.
  Internal `.dot` param names (`sur_repo_dir`) are grandfathered — leave them.

## Architecture (the one rule that governs everything)

`pipelines/simulated-user-research.dot` is the **sole logic home** (prompts, stages,
retry policy). The lib, CLI, and tool module are thin adapters that orchestrate runs
of it — they must never reimplement or fork pipeline logic. See PRINCIPLES.md.

## Gates before "done"

```bash
uv run pytest tests/ -q                                  # main suite
(cd modules/tool-simulated-user-research && uv run pytest -q)
uv build                                                  # if packaging/pyproject changed
```
Plus `python_check` clean on changed Python. If you touched the `.dot`: re-validate
with the engine's own parser (see SMOKE_TESTS.md). If you touched pipeline mechanics
(guards, wrapper, prompts): run the cheap resume smoke; browser-node or orchestration
changes need a full live round (see the verification gradient in the PR template).

## Pitfalls that bit us (do not rediscover these)

1. **loop-agent ends a session on any text-only reply.** Persona role-play makes
   models narrate → sessions died mid-browse, reported `success`, wrote nothing.
   That is WHY browser nodes are `parallelogram` tool nodes shelling
   `scripts/run_browser_node.py` (single-shot session; the JSON response IS the
   deliverable). Never convert them back to `box` nodes; prompt discipline decays.
2. **Verify by artifact contract, never byte count or agent self-report.**
   `scripts/validate_artifact.py` is the oracle. Contract and prompt must agree on
   formats (a citation-regex/prompt mismatch once rejected a good artifact 3×).
   Resume guards (`check_*`) use lenient run-id mode; `verify_*` use `--require-exact`.
3. **Wheel data**: the CLI runs from `_bundled/` data force-included in the wheel.
   If you add runtime files (pipelines/scripts/personas), add them to the
   `force-include` table in pyproject or wheel installs silently lose them.
4. **Tool-module deps**: the Amplifier module activator uses uv's legacy pip
   resolver — it rejects *transitive* git-URL deps. The tool module's pyproject
   declares the full transitive git closure on purpose. Keep it in sync.
5. **agent-browser on Linux ARM64**: Chrome-for-Testing ships no arm64 builds;
   `agent-browser install` exits 2. Remediation lives in `doctor` and README
   (`AGENT_BROWSER_EXECUTABLE_PATH` → Playwright `headless_shell`, `--no-sandbox`).
6. **`amplifier run -B` needs a `file://` URL** — a bare path is treated as a
   registered bundle name and won't resolve.
7. **`--on-human-gate console`** needs engine ≥ attractor PR #95 (on `@main`);
   `stop` is the default unattended ending and is SUCCESS, not failure.
8. **`attractor` is a generic binary name — presence ≠ identity.** An unrelated
   package shipped its own `attractor` earlier on PATH; `shutil.which()`-first
   resolution shelled out to it and the run died with an inscrutable argparse
   `unrecognized arguments` error, while `doctor` had reported **[OK]** because
   it only checked existence. Resolution is now **interpreter-sibling first**
   (the engine installed alongside us), PATH only as fallback, and every
   candidate is **identity-probed** (`<binary> run --help` must advertise
   `--param`, `--logs-root`, `--on-human-gate`) before use — rejects are named
   in the loud failure. Generalize the lesson: any preflight check must
   validate *capability*, not presence (same class as the browser-launchability
   check). If you add a dependency on an external binary, probe what it can do.

## Workflow

Branch → PR → merge (main is ruleset-protected: PR + 1 approval, linear history;
admins bypass via the PR path only). Conventional commits with the Amplifier
co-author trailer. Populate the PR template from real evidence — paste, don't
paraphrase. Lessons learned go back into these files before you call work done.
