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
