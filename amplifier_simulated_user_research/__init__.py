"""amplifier_simulated_user_research -- L2 lib over the simulated-user-research
attractor pipeline.

The pipeline's `.dot` graph (pipelines/simulated-user-research.dot) is the
single logic home: prompts, stages, retry policy, and the revision loop all
live there. This package is a thin orchestration layer -- it builds and runs
`attractor run ...` invocations and inspects the resulting artifacts. It does
not reimplement any pipeline behavior.

Public API:
    RoundConfig  -- per-project configuration, loaded from a project YAML.
    run_round    -- run one research round; returns a RoundResult.
    RoundResult  -- outcome of a run_round() call.
    doctor       -- environment diagnostics; returns a list of DoctorCheck.
    DoctorCheck  -- one diagnostic result.
"""

from .config import RoundConfig
from .doctor import DoctorCheck, doctor
from .runner import RoundResult, run_round

__all__ = [
    "RoundConfig",
    "run_round",
    "RoundResult",
    "doctor",
    "DoctorCheck",
]
