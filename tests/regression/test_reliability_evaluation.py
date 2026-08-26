from __future__ import annotations

from pathlib import Path

import pytest

from repopilot.evaluation import run_reliability_evaluation


@pytest.mark.docker
def test_canonical_reliability_matrix_passes_with_zero_safety_violations(tmp_path: Path) -> None:
    report = run_reliability_evaluation(Path("benchmarks/cases"), tmp_path / "reliability")

    aggregate = report["aggregate"]
    assert aggregate["scenarios"] == 8
    assert aggregate["scenarios_passed"] == 8
    assert aggregate["recoverable_scenarios"] == 6
    assert aggregate["recoverable_scenarios_succeeded"] == 6
    assert aggregate["unclassified_error_count"] == 0
    assert report["fault_injection"] is True
    assert report["fault_schedule"]["content_hash"].startswith("sha256:")
    assert report["profile_acceptance"]["passed"] is True
    for field in (
        "unsafe_retry_count",
        "duplicate_mutation_count",
        "revision_divergence_count",
        "budget_overshoot_count",
    ):
        assert aggregate[field] == 0

    scenarios = {scenario["scenario_id"]: scenario for scenario in report["scenarios"]}
    assert all(scenario["passed"] for scenario in scenarios.values())
    ambiguous = scenarios["ambiguous-patch-reconcile"]
    assert ambiguous["recovery"]["decisions"][0]["action"] == "reconcile_mutation"
    assert ambiguous["safety"]["duplicate_mutation_count"] == 0
    assert ambiguous["observed"]["workspace_revision"] == 1
    assert ambiguous["baseline_comparison"]["same_final_diff"] is True

    permanent = scenarios["permanent-model-stop"]
    provider = next(item for item in permanent["failure_analysis"]["classifications"] if item["label"] == "model.provider_error")
    assert provider["recoverability"] == "non_retryable"

    deadline = scenarios["deadline-exhausted-during-retry"]
    provider = next(item for item in deadline["failure_analysis"]["classifications"] if item["label"] == "model.provider_error")
    assert provider["recoverability"] == "retryable_unrecovered"
