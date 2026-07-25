# KNOWN_ISSUES.md

Deliberately deferred, with intent. Not a bug tracker — pitfalls live in AGENTS.md.
Full provenance for the roadmap items: `docs/BEST-IN-CLASS-BACKLOG.md`.

## Deferred (agreed)

- **Dependencies track `@main`, not SHAs.** Appropriate while the engine
  (amplifier-bundle-attractor) is co-evolving with this repo; SHA-pin for
  reproducible builds once the pipeline stabilizes. (Backlog Gate 3.)
- **No measured cost-per-round dollar figure.** Wall-clock and per-stage timing
  land in `rounds.jsonl`; token/dollar receipts are hardware/model dependent and
  deliberately unquoted until measured across a few rounds. (Backlog item 11.)
- **External pilots not yet run.** Finding-validity on a product the authors did
  NOT build is n=0. The precision metric exists precisely so pilots can measure
  it; run 2–3 time-boxed pilots (author not driving) before making validity
  claims beyond our own products. (Backlog item 15.)
- **Internal `sur_` identifiers remain** (`sur_repo_dir` graph param, `.dot`
  header comments). The acronym ban (AGENTS.md §Naming) covers user-facing
  surfaces; renaming proven internal plumbing is churn without user value.
  Revisit only if the graph gets a breaking param change anyway.
- **The engine's binary name (`attractor`) is generic and not ours to change.**
  Unrelated packages ship a command by that name, so a PATH collision is a
  permanent environmental hazard. We defend locally instead of asking upstream
  to rename: sibling-first resolution plus an identity probe of every candidate
  (AGENTS.md pitfall 8). Revisit only if upstream namespaces its console script.
- **The L3 root bundle still editable-installs this project into the Amplifier
  CLI venv.** That is deliberate — the tool module imports
  `amplifier_simulated_user_research`, and the bundle-package install is how it
  gets there. But it inherits the same fragility that broke the browser bundle:
  if the CLI venv holds attractor pins different from our `@main`, the activator's
  `--overrides` make the install fail, and `-B .../simulated-user-research.bundle.md`
  dies before any tool runs (reproduced 2026-07-24). The browser bundle is now
  immune (it needs none of our code); the L3 path is not. Real fix when we choose
  to take it: have `modules/tool-simulated-user-research` depend on the published
  `amplifier-app-simulated-user-research` git URL like it already does for the
  attractor modules, so no bundle needs the repo-root install at all.
- **Revision cap finalizes gracefully.** Hitting the cap (3) appends a visible
  note and completes the run rather than hard-stopping — deliberate: at the cap,
  a spec-in-hand beats a dead pipeline. The human gate remains the authority.
- **No full-round CI.** A real round costs ~1–2 h + LLM spend; CI covers units
  only. The verification gradient (SMOKE_TESTS.md) is the compensating control.

## Future ideas (unscheduled)

- Round-over-round diffing: round N auto-re-checks round N−1's findings and the
  spec reports fixed/regressed status per finding ID (the ledger already links
  runs; the synthesis prompt does the re-check — a reporting layer would make it
  first-class).
- Roster growth beyond 3 personas requires graph shape changes (persona chain is
  fixed at 3 sequential nodes); template-generate the graph if a real consumer
  needs N≠3.
