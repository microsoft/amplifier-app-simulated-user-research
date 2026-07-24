# Best-in-Class Backlog — Council Reviews Synthesis

**Date:** 2026-07-23 · **Inputs:** engineering council (6 lenses, unanimous CONCERN) ·
product council (6 lenses: 5 CONCERN + 1 held FAIL) · design panel (design-system-architect +
voice-strategist) · industry research brief (NN/g methodology; Batzner et al. 2025 AIES on
LLM-persona validity; practitioner patterns for honest synthetic research).

## The one-paragraph verdict (all four streams converge)

The engine is genuinely good — files-as-contract, ground-truth verification, the structural
single-shot fix, proven-manually-then-encoded order all drew explicit credit. What it is NOT
yet is what its name claims. As built, it is an **automated pre-flight product audit with
persona lenses** (observed facts: dead taps, contradictory copy, measured waits, network
calls — real and reproducible) wearing the costume of **user research** (persona feelings and
verdicts — one model in three costumes, presented at equal authority). Best-in-class is
reachable and cheap, but it runs through three gates in order: **(1) make the oracle inspect
instead of weigh, (2) make the deliverable epistemically honest, (3) instrument outcomes.**
Industry research independently endorses exactly this: the honest category is
"persona-driven heuristic audit," and the emerging best practices for credible synthetic
research are confidence tiers, reproducible prompting, and human verification loops.

## The smoking gun (found independently by all 6 engineering lenses)

`capture-notes.md` in the showcase run is 1,800 bytes, documents 4 of 11 screens, and ends
with `## In Progress — Additional Screens (to be completed)` — yet passed its byte-floor
verify gate, was grandfathered by the identical resume guard in every later run, and
synthesis then certified "Missing inputs: None." **A byte count is a length check wearing a
completeness costume.** Corollary trap: check and verify share the same floor, so a partial
artifact blocks its own completion forever.

## Unified priority backlog

### Gate 1 — Truth mechanics (do first; decision-independent)

1. **Artifact contracts replace byte floors** (all 6 eng lenses): required sections present;
   cited screenshots must exist in `screens/`; reject "to be completed"/TODO/`(in progress)`
   placeholders; split resume-guard semantics from quality-gate semantics so partials heal
   instead of entombing.
2. **Prove the browser ran** (tester-breaker; kills the hallucinated-persona class): emit a
   machine-readable manifest of agent-browser invocations per session; fail the node on zero
   real navigations. A dead target URL must be a loud failure, not an 11KB plausible fiction.
3. **The instrument trio** (product council's held FAIL; ~an afternoon): `findings.json`
   (stable finding IDs) · persisted gate verdict + per-finding real/noise/won't-fix triage
   (~30s per P1) · `rounds.jsonl` ledger with prior-round linkage, and round N re-checks
   round N−1's P1s (makes the same-day-regression catch repeatable on purpose). Real Run IDs
   everywhere — `Run ID: unknown` never ships again.
4. **Revision staleness + retry substrate** (tester-breaker): hash/delete the spec before a
   revision pass and hard-stop if synthesis didn't rewrite it; consider single-shot synthesis
   (narrate-and-die is still live there); `&&`-guard counter writes; clear counters on
   hard_stop.
5. **Pressurize the never-run segments** (restless-old-brian): one clean fresh-directory E2E
   pass; execute the single-shot capture path in anger; answer the gate all three ways
   (approve / revise / cap).

### Gate 2 — Epistemic honesty (the differentiator; mostly prompt/template edits)

6. **Evidence-tier vocabulary, stamped on every finding and quote** (design panel + product
   council + industry brief, identical demand from four directions):
   `[OBSERVED]` (machine-checkable, must carry repro) · `[SIMULATED]` (persona judgment —
   product of the brief, not a human) · `[INFERRED]` (synthesis hypothesis, unverified).
   Confirmation vocabulary: `REPRODUCED (N sessions)` / `OBSERVED (1 session)` /
   `RAISED BY N/3 PERSONAS` / `RELATED FINDING` — "related" is never "confirmed."
7. **Rename the false words** (voice-strategist): "Verbatim Confusions" →
   "In-Character Reactions (simulated think-aloud)"; verdicts retitled
   "Verdict (simulated, per this persona's stated bar)"; provenance banner atop every persona
   report and the spec ("one LLM role-playing… observed behaviors are real and reproducible;
   reactions and verdicts are simulation — hypotheses, not testimony").
8. **Spec header block for the 60-second read** (design-system-architect): verdict table
   (persona / verdict / dealbreaker) → findings-at-a-glance table (sev / title / evidence tier
   / confirmed-by / fix rank) → 3-sentence summary. ONE severity taxonomy (demotions carry a
   stated reason; Parked can't hold above MEDIUM). **Repro steps travel into the spec** —
   the persona bug tables are the crown jewel; the deliverable must carry them.
9. **The honest words** (product council + voice): README "full user-research round… zero
   humans" → "user-research-style audit round"; the what-this-is-and-isn't paragraph
   ("it makes your first real user session worth running — it does not replace it");
   severity justified by the product's own promise, never by invented backstory; real-user
   predictions marked HYPOTHESIS. `--on-human-gate fail` → `stop` (alias kept).
10. **Persona briefs teach instead of trap** (all streams): `personas/_TEMPLATE.md` with
    PORTABLE vs REPLACE-layer markers and the named load-bearing mechanism (ONE falsifiable
    temperament rule + pre-declared yes/no bar); `PROBE:` tag convention in briefs AND
    reports (scripted probes are legitimate method — reporting them as spontaneous
    discoveries is not); product-contamination banner in `asur init` + doctor warning on
    unchanged defaults; `validate()` rejects its own REPLACE-ME sentinels.

### Gate 3 — Operational maturity

11. **Count the money, name the run** (crusty + bet-sizer): per-stage tokens/cost/wall-clock
    receipt mined from `.attractor-logs`, in the spec footer.
12. **Test what breaks; pin what we ride** (crusty/sam/breaker): golden tests for the
    wrapper's JSON/banner parsing and verify/retry arithmetic; SHA-pin @main deps; file the
    upstream loop-agent issue (text-only-reply completion) so the workaround isn't permanent.
13. **Credential/shell hygiene** (crusty/breaker): argv-pass params instead of `${...}` shell
    interpolation; persona names validated as path components; no plaintext keys in examples.
14. **Gate audits the research, not just the spec** (intent-keeper/user-advocate): attach
    upstream anomalies (hard-stop history, retry counts, capture gaps) beside the spec at the
    gate; the human can always overrule the revision cap.
15. **External pilots (2–3, author not driving) — only AFTER gates 1–2** (bet-sizer): produce
    the first measured precision number; finding-validity on a product we didn't build is n=0
    today. Soft clock: run within ~a product cycle.

## Metrics (product council, consolidated to three)

1. **Precision at gate** — % of P1s a human graded real; observed and simulated tiers graded
   separately. 2. **Fix-conversion** — % of P1s merged-fixed within 14 days. 3. **Cost +
   wall-clock receipt per round.** (Second-run-within-30-days becomes the retention metric
   the moment pilots exist.)

## DON'T build (unanimous unless noted)

Arc-(c) market ambitions under the "research" banner · persona-generation tooling or roster
expansion (both real rounds bottlenecked on mechanism, never persona shortage) · simulated
NPS/willingness-to-pay ("'Ken said no' is the fiction half of the output") · a fifth
consumption surface, or deepening the four in lockstep · SaaS/hosted runs · dashboards ·
auto-fix (spec→PR) automation · heavy browser-plumbing moat investment (agentic-browser
parity is coming; the moat is the method + the measured precision number).

## Owner decisions (resolved 2026-07-23)

- **A. The name/claim — RESOLVED: name stays, framing copy repositions.** Per the
  voice-strategist's finding ("the name 'Simulated User Research' itself is honest; keep
  it"), the tool keeps its name while every claim-bearing sentence adopts the audit-filter
  framing ("a user-research-style audit round… it makes your first real user session worth
  running — it does not replace it") and the moat sentence (real browser · real instance ·
  reproducible findings) enters the positioning.
- **B. The gift shop — RESOLVED: ALL layers stay** (owner call, overruling
  cranky-old-sam's demolition recommendation). L1 attractor + Resolve sidecar, L2 lib,
  L3 tool module, L4 CLI are all retained as strategic surface area. Consequence accepted:
  the adapters must stay tested and integrated (backlog item 12's golden tests and the
  instrument trio become more load-bearing, not less), and every roadmap item carries the
  multi-surface integration cost knowingly.

## Industry grounding (research brief highlights)

NN/g: 5 users find ~85% of problems — bounded rounds; clustering = signal, but LLM-persona
homogeneity inflates frequency, so flag it. Severity = frequency × impact × persistence,
kept explicit, never collapsed. Heuristic evaluation and usability testing are complementary,
not interchangeable — this tool is the former with a persona twist. Batzner et al. 2025
(63-study review): ecological-validity and persona-prompt-leading failures are the field's
central defects — exactly what the PROBE tags and evidence tiers counter. Practitioner
consensus on what synthetic research is FOR: early-stage filtering, edge-case exploration,
hypothesis generation, rapid iteration triage. NOT for: preference/desirability, pricing,
emotional resonance. Emerging honesty practices adopted here: confidence tiers, reproducible
prompting (model/temp disclosed), human verification loop at the gate, plural-method
triangulation before prioritizing.
