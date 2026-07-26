"""Tests for harness provenance -- which build produced a round's findings.

The incident these guard: a round reported a false "control X does nothing"
because it ran on a build predating the click-discipline prompt fix, and
nothing in its records said so. Catching it took a manual grep of the
installed wrapper. These tests pin the automated replacement.

Every test builds a FAKE repo root in tmp_path -- the real
scripts/run_browser_node.py and pipelines/*.dot are never mutated.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from amplifier_simulated_user_research.config import RoundConfig
from amplifier_simulated_user_research.provenance import (
    describe_provenance,
    harness_provenance,
    provenance_differences,
)

WRAPPER_REL = Path("scripts") / "run_browser_node.py"
PIPELINE_REL = Path("pipelines") / "simulated-user-research.dot"


def _fake_repo(
    tmp_path: Path,
    *,
    wrapper: str = "# wrapper v1\nCLICK DISCIPLINE\n",
    pipeline: str = "digraph { a -> b }\n",
) -> Path:
    """Build a fake sur_repo_dir with the two prompt-shaping surfaces."""
    root = tmp_path / "fake-repo"
    for rel, content in ((WRAPPER_REL, wrapper), (PIPELINE_REL, pipeline)):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def _config(repo_root: Path | None, **overrides: Any) -> RoundConfig:
    kwargs: dict[str, Any] = dict(
        target_url="http://127.0.0.1:8892",
        seed_command="true",
        seed_cwd="/tmp",
        personas_dir="/tmp/personas",
        output_dir="/tmp/output",
        app_source_hint="/tmp/app",
        personas=["marisol", "dev", "ken"],
        api_key="test-key",
    )
    if repo_root is not None:
        kwargs["sur_repo_dir"] = str(repo_root)
    kwargs.update(overrides)
    return RoundConfig(**kwargs)


class TestHarnessProvenance:
    def test_records_tool_version(self, tmp_path):
        provenance = harness_provenance(
            _config(_fake_repo(tmp_path)), include_engine=False
        )
        # This package is installed in the test environment, so the version
        # is derivable; its exact value is not this test's business.
        assert provenance["tool_version"]

    def test_hashes_both_prompt_surfaces(self, tmp_path):
        provenance = harness_provenance(
            _config(_fake_repo(tmp_path)), include_engine=False
        )
        assert len(provenance["wrapper_sha256"]) == 12
        assert len(provenance["pipeline_sha256"]) == 12
        assert provenance["wrapper_sha256"] != provenance["pipeline_sha256"]

    def test_wrapper_hash_changes_when_prompt_surface_changes(self, tmp_path):
        """THE regression this feature exists for: a prompt-surface edit
        (e.g. adding the click-discipline block) must be visible in the
        ledger even though the package version never changes."""
        before = harness_provenance(
            _config(_fake_repo(tmp_path, wrapper="# wrapper without the fix\n")),
            include_engine=False,
        )
        after = harness_provenance(
            _config(
                _fake_repo(
                    tmp_path,
                    wrapper="# wrapper without the fix\n## CLICK DISCIPLINE\n",
                )
            ),
            include_engine=False,
        )

        assert before["wrapper_sha256"] != after["wrapper_sha256"]
        # ...and the version alone would NOT have caught it:
        assert before["tool_version"] == after["tool_version"]

    def test_pipeline_hash_changes_independently_of_wrapper(self, tmp_path):
        base = harness_provenance(_config(_fake_repo(tmp_path)), include_engine=False)
        changed = harness_provenance(
            _config(_fake_repo(tmp_path, pipeline="digraph { a -> b -> c }\n")),
            include_engine=False,
        )

        assert base["pipeline_sha256"] != changed["pipeline_sha256"]
        assert base["wrapper_sha256"] == changed["wrapper_sha256"]

    def test_identical_content_hashes_identically(self, tmp_path):
        """Same bytes -> same fingerprint, so 'no mismatch' is meaningful."""
        first = harness_provenance(_config(_fake_repo(tmp_path)), include_engine=False)
        second = harness_provenance(_config(_fake_repo(tmp_path)), include_engine=False)
        assert first == second

    def test_missing_files_are_omitted_not_fabricated(self, tmp_path):
        """An absent field beats an invented one (PRINCIPLES: no fabrication)."""
        empty_root = tmp_path / "no-surfaces-here"
        empty_root.mkdir()

        provenance = harness_provenance(_config(empty_root), include_engine=False)

        assert "wrapper_sha256" not in provenance
        assert "pipeline_sha256" not in provenance
        assert provenance.get("tool_version")  # still honest about what it knows

    def test_include_engine_false_runs_no_engine_resolution(
        self, tmp_path, monkeypatch
    ):
        """Triage must not pay a subprocess probe just to print a warning."""

        def exploding_resolution(checkout):
            raise AssertionError(
                "engine must not be resolved when include_engine=False"
            )

        monkeypatch.setattr(
            "amplifier_simulated_user_research.runner.resolve_attractor_resolution",
            exploding_resolution,
        )

        provenance = harness_provenance(
            _config(_fake_repo(tmp_path)), include_engine=False
        )

        assert "engine_path" not in provenance
        assert "engine_source" not in provenance

    def test_engine_fields_recorded_when_resolvable(self, tmp_path, monkeypatch):
        from amplifier_simulated_user_research.runner import AttractorResolution

        monkeypatch.setattr(
            "amplifier_simulated_user_research.runner.resolve_attractor_resolution",
            lambda checkout: AttractorResolution(
                command=["/venv/bin/attractor"], source="interpreter-sibling"
            ),
        )

        provenance = harness_provenance(_config(_fake_repo(tmp_path)))

        assert provenance["engine_path"] == "/venv/bin/attractor"
        assert provenance["engine_source"] == "interpreter-sibling"

    def test_unresolvable_engine_omits_fields_without_raising(
        self, tmp_path, monkeypatch
    ):
        """A broken engine is run_round's failure to report -- provenance
        just records less, and never raises while describing a run."""

        def no_engine(checkout):
            raise RuntimeError("no usable attractor engine found")

        monkeypatch.setattr(
            "amplifier_simulated_user_research.runner.resolve_attractor_resolution",
            no_engine,
        )

        provenance = harness_provenance(_config(_fake_repo(tmp_path)))

        assert "engine_path" not in provenance
        assert provenance["wrapper_sha256"]  # the rest still recorded

    def test_never_raises_on_a_broken_config(self, tmp_path, monkeypatch):
        config = _config(_fake_repo(tmp_path))
        monkeypatch.setattr(
            type(config),
            "resolved_sur_repo_dir",
            lambda self: (_ for _ in ()).throw(OSError("boom")),
        )

        provenance = harness_provenance(config, include_engine=False)

        assert "wrapper_sha256" not in provenance  # degraded, not crashed


class TestProvenanceDifferences:
    def test_no_differences_when_identical(self):
        provenance = {"tool_version": "0.1.0", "wrapper_sha256": "aaa"}
        assert provenance_differences(provenance, dict(provenance)) == []

    def test_reports_the_differing_field_with_both_values(self):
        differences = provenance_differences(
            {"tool_version": "0.1.0", "wrapper_sha256": "old12345678"},
            {"tool_version": "0.1.0", "wrapper_sha256": "new12345678"},
        )
        assert len(differences) == 1
        assert "wrapper_sha256" in differences[0]
        assert "old12345678" in differences[0]
        assert "new12345678" in differences[0]

    def test_reports_every_differing_field(self):
        differences = provenance_differences(
            {"tool_version": "0.1.0", "wrapper_sha256": "a", "pipeline_sha256": "b"},
            {"tool_version": "0.2.0", "wrapper_sha256": "c", "pipeline_sha256": "d"},
        )
        assert len(differences) == 3

    def test_engine_fields_are_not_compared(self):
        """Engine path/source vary by machine and venv -- comparing them
        would train people to ignore a warning that should be rare."""
        differences = provenance_differences(
            {"wrapper_sha256": "same", "engine_path": "/a/attractor"},
            {"wrapper_sha256": "same", "engine_path": "/b/attractor"},
        )
        assert differences == []

    def test_old_record_without_harness_reports_nothing(self):
        """Records written before this feature carry no harness at all."""
        assert provenance_differences(None, {"wrapper_sha256": "abc"}) == []
        assert provenance_differences({}, {"wrapper_sha256": "abc"}) == []

    def test_field_missing_from_one_side_is_skipped(self):
        """Partial provenance (e.g. a wheel without the .dot) is not a
        mismatch -- absence is unknown, not different."""
        differences = provenance_differences(
            {"wrapper_sha256": "abc"},
            {"wrapper_sha256": "abc", "pipeline_sha256": "def"},
        )
        assert differences == []


class TestDescribeProvenance:
    def test_absent_provenance_says_it_predates_the_field(self):
        assert "predates" in describe_provenance(None)
        assert "predates" in describe_provenance({})

    def test_summarizes_fields_sorted(self):
        summary = describe_provenance(
            {"wrapper_sha256": "abc", "tool_version": "0.1.0"}
        )
        assert summary == "tool_version=0.1.0 wrapper_sha256=abc"
