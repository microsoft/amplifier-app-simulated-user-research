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

- **`run_research_round`** -- runs one full research round from a
  `project.yaml` config (see the repo README, or scaffold one with
  `asur init`). It seeds a scratch instance, captures every screen, runs
  parallel IA/responsive design reviews, puts N personas through real
  first-run browser sessions, and synthesizes everything into one
  implementation-ready spec.

  This can take MANY MINUTES (real LLM calls + real browser sessions) --
  do not assume a long-running call means failure. When it returns
  `status: "gate_reached"`, that is SUCCESS for an unattended round, not a
  failure: the pipeline finished its work and is deliberately waiting at
  the human-approval gate (`research-spec.md` exists in `output_dir`). Read
  the spec and report it back to the human rather than treating the gate
  as an error.

If a round fails for a reason that isn't obvious from the tool's output,
inspect the returned `logs_dir` before guessing -- it contains the full
per-node engine logs for that run.
