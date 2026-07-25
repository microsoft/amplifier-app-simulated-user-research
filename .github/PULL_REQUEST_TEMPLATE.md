## Summary

<!-- What changed and why. If prompts/stages changed, say what behavior difference to expect in artifacts. -->

## Verification checklist

<!-- For each item: paste real evidence, mark N/A with a reason, or STOP and surface the gap.
     Never check a box you cannot back. -->

- [ ] Unit tests pass — `uv run pytest tests/ -q` (paste count) and
  `cd modules/tool-simulated-user-research && uv run pytest -q`
- [ ] `python_check` clean on changed Python
- [ ] Packaging: `uv build` succeeds **and** new runtime files added to the wheel
  `force-include` table (N/A if no packaging/data changes)
- [ ] `.dot` changes: graph re-validated with the engine's own parser
  (see SMOKE_TESTS.md §graph) — paste node/edge counts (N/A if graph untouched)
- [ ] Pipeline-mechanics changes (guards, validator, wrapper, prompts): cheap resume
  smoke run reaches the human gate (SMOKE_TESTS.md §resume) — paste the gate line
  (N/A if mechanics untouched)
- [ ] Browser-node or orchestration changes: one full live round completed, artifacts
  passing their contracts — link the output dir / ledger line (N/A with reason)
- [ ] The banned four-letter acronym (see AGENTS.md §Naming) not introduced anywhere
  (`grep -rinE "\bas[u]r\b" . --exclude-dir=.git` clean — the pattern is written
  self-exempt on purpose)
- [ ] Docs updated where behavior changed (README / docs/USAGE.md / AGENTS.md pitfalls)

## Evidence

<!-- Paste command output, gate lines, ledger records, artifact listings. Links > prose. -->
