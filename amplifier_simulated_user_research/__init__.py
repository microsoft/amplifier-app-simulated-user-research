"""amplifier_simulated_user_research -- L2 lib over the simulated-user-research
attractor pipeline.

The pipeline's `.dot` graph (pipelines/simulated-user-research.dot) is the
single logic home: prompts, stages, retry policy, and the revision loop all
live there. This package is a thin orchestration layer -- it builds and runs
`attractor run ...` invocations, inspects the resulting artifacts, and keeps
the run ledger (rounds.jsonl) + gate-triage records honest. It does not
reimplement any pipeline behavior.

Public API:
    RoundConfig       -- per-project configuration, loaded from a project YAML.
    run_round         -- run one audit round; returns a RoundResult.
    RoundResult       -- outcome of a run_round() call (incl. run_id + ledger path).
    generate_run_id   -- fresh run id (r-YYYYMMDD-HHMMSS).
    doctor            -- environment diagnostics; returns a list of DoctorCheck.
    DoctorCheck       -- one diagnostic result (ok + optional warn).
    load_findings     -- read a round's findings.json (the graph's contract).
    run_triage        -- grade findings real/noise/wont-fix via an ask callable.
    record_triage     -- persist gate verdict + triage into rounds.jsonl.
    precision_summary -- the precision-at-gate line (per evidence tier).
    latest_round      -- most recent record from a project's rounds.jsonl.
"""

from .config import RoundConfig
from .doctor import DoctorCheck, doctor
from .runner import RoundResult, generate_run_id, run_round
from .triage import (
    latest_round,
    load_findings,
    precision_summary,
    record_triage,
    run_triage,
)

__all__ = [
    "RoundConfig",
    "run_round",
    "RoundResult",
    "generate_run_id",
    "doctor",
    "DoctorCheck",
    "load_findings",
    "run_triage",
    "record_triage",
    "precision_summary",
    "latest_round",
]
