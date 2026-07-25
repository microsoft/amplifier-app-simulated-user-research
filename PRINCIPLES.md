# PRINCIPLES.md — architectural invariants

Read before designing changes. These were each paid for with a real failure;
`docs/BEST-IN-CLASS-BACKLOG.md` records the full council-review provenance.

## 1. The `.dot` is the sole logic home

`pipelines/simulated-user-research.dot` owns prompts, stage order, retry policy, and
gate semantics. Lib/CLI/tool-module are thin adapters that *invoke* the graph
(per the amplifier-tool-leverage-patterns DRY rule). One exception, documented where
it lives: browser-session prompts reside in `scripts/run_browser_node.py` because
those stages run as single-shot sessions outside the graph's agent loop.

## 2. Simulation must never impersonate measurement

The product's honesty model, enforced end-to-end: every finding and quote carries an
evidence tier — `[OBSERVED]` (machine-checkable, must carry repro steps),
`[SIMULATED]` (persona judgment — the brief's product, not a human's), `[INFERRED]`
(hypothesis). Confirmation vocabulary: `REPRODUCED` / `OBSERVED_SINGLE` /
`RAISED_BY_PERSONAS` / `RELATED` — thematic adjacency is never "confirmed".
Persona reports open with a provenance banner; severity is justified by the
product's own promise, never persona backstory; scripted probes are tagged
`PROBE`; real-user predictions are tagged `HYPOTHESIS`. This is the tool's claim
to exist: an audit filter run before real user research, never a replacement.
Any change that lets simulated content borrow observed authority is wrong by
definition, regardless of how useful it looks.

## 3. Verification trusts artifacts, not agents

Success = the artifact on disk satisfies its contract (`scripts/validate_artifact.py`:
required sections, placeholder rejection, citation existence, findings.json schema)
— never an agent's self-report, never a byte count. Browser stages must additionally
prove the browser ran (engine-trace session manifests; zero real navigations = no
report = loud failure). Resume guards (`check_*`) accept complete artifacts from
prior runs; production gates (`verify_*`) require this run's stamp (`--require-exact`).

## 4. Fail loud, resume cheap

Every stage: bounded retries → hard stop with a diagnosis naming the artifact,
the manifest, and the logs. Files are the contract: any crash resumes by re-running
only what's incomplete. A partial artifact must fail its guard and heal — never
skip-as-done (the byte-floor entombing bug), never block forever.

Preflight checks validate **capability, not presence**: an external dependency is
healthy only if it can do the specific thing we need (the browser actually
launches; the `attractor` binary actually advertises our flags). A check that
merely confirms a file exists manufactures a false [OK] and moves the failure
somewhere far less legible — the diagnosis belongs at the earliest honest moment.

## 5. Outcomes are instrumented

Every run has a run_id stamped through every artifact; every round appends to
`rounds.jsonl` (status, wall-clock, prior-run linkage, gate verdict, per-finding
human triage). The three product metrics: precision at gate (observed and simulated
tiers reported separately), fix-conversion, cost per round. `Run ID: unknown` never
ships.

## 6. Upstream linkage

The engine is the attractor pipeline-runner (microsoft/amplifier-bundle-attractor,
canonical spec: strongdm/attractor). Intentional local deltas: browser stages as
single-shot tool nodes (see AGENTS.md pitfall #1); freeform gate feedback rides the
bundle's declared EXTENSIONS §19. When engine behavior is ambiguous, read the
canonical spec before working around it — two upstream gaps found here became
merged contributions (#93, #95) rather than local hacks.
