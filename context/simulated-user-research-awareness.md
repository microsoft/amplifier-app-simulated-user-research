## Simulated User Research -- awareness

This bundle exposes two agent-callable tools over the simulated-user-research
attractor pipeline (see `pipelines/simulated-user-research.dot` -- that `.dot`
file is the pipeline's logic home; these tools only orchestrate runs of it,
they never reimplement its stages, prompts, or retry policy):

- **`research_doctor`** -- environment diagnostics (the `attractor` CLI, the
  `agent-browser` CLI, the LLM provider API key, and this repo's own
  pipeline/script files). Cheap, read-only, and safe to call any time --
  never runs the pipeline itself. Call this FIRST if you are unsure the
  environment is ready, or if `run_research_round` reports a failure.

- **`run_research_round`** -- runs one user-research-style AUDIT round from a
  `project.yaml` config (see the repo README, or scaffold one with
  `asur init`). It seeds a scratch instance, captures every screen, runs
  parallel IA/responsive design reviews, puts N personas through real
  first-run browser sessions, and synthesizes everything into one
  implementation-ready spec plus a machine-readable `findings.json`.

  This can take MANY MINUTES (real LLM calls + real browser sessions) --
  do not assume a long-running call means failure. When it returns
  `status: "gate_reached"`, that is SUCCESS for an unattended round, not a
  failure: the pipeline finished its work and is deliberately waiting at
  the human-approval gate (`research-spec.md` exists in `output_dir`). Read
  the spec and report it back to the human rather than treating the gate
  as an error.

When you relay results to a human, keep the evidence tiers honest: what the
round OBSERVED (dead taps, contradictory copy, measured waits, network
calls) is real and reproducible -- each finding carries repro steps. What
the personas FELT or CONCLUDED is simulation (one model role-playing three
briefs) -- present persona reactions and verdicts as hypotheses to test
with real users, never as user testimony. This tool makes a first real
user session worth running; it does not replace one.

Each run has a `run_id` and appends a record to `<output_dir>/rounds.jsonl`
(timings, artifacts, prior-run linkage). After a human answers the gate,
the findings should be graded (real / noise / wont-fix) -- point the human
at `asur triage --config <project.yaml>`; the graded verdicts persist into
the ledger and yield the precision-at-gate metric.

If a round fails for a reason that isn't obvious from the tool's output,
inspect the returned `logs_dir` before guessing -- it contains the full
per-node engine logs for that run.
