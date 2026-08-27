from __future__ import annotations

import hashlib
import json
from pathlib import Path


RUBRIC = Path("configs/evaluation/deepseek_behavioral_admission_review.v1.json")
REVIEW = Path("docs/checkpoints/DEEPSEEK_BEHAVIORAL_ADMISSION_REVIEW.json")


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_behavioral_admission_is_separate_and_strict_result_is_unchanged() -> None:
    rubric = json.loads(RUBRIC.read_text(encoding="utf-8"))
    review = json.loads(REVIEW.read_text(encoding="utf-8"))

    assert review["strict_compatibility_decision"] == "NOT COMPATIBLE"
    assert review["strict_compatibility_result"] == "9/12 protocol-complete (75%) against a frozen threshold of at least 80%"
    assert review["behavioral_admission_decision"] == "ADMITTED FOR SMALL BEHAVIORAL PILOT"
    assert rubric["admission_rule"]["strict_protocol_score_used_as_admission_gate"] is False
    assert rubric["admission_rule"]["strict_compatibility_verdict_must_remain"] == "NOT COMPATIBLE"
    assert review["strict_vs_behavioral_distinction"]["retroactive_rescore"] is False
    assert review["preserved_evidence"]["strict_scores_or_labels_modified"] is False
    assert review["recommended_next_step"]["swebench_started"] is False


def test_predeclared_rubric_and_frozen_evidence_remain_byte_identical() -> None:
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    evidence = review["preserved_evidence"]

    assert _sha256(RUBRIC) == review["frozen_rubric"]["file_sha256"]
    assert _sha256(Path("docs/checkpoints/MODEL_COMPATIBILITY_DEEPSEEK.json")) == evidence["deepseek_checkpoint_sha256"]
    assert _sha256(Path("docs/checkpoints/MODEL_COMPATIBILITY_DEEPSEEK_RUNS/MODEL_COMPATIBILITY_GATE.json")) == evidence["deepseek_raw_report_sha256"]
    assert _sha256(Path("configs/evaluation/model_compatibility.json")) == evidence["strict_profile_sha256"]
    assert _sha256(Path("benchmarks/model_compatibility/cases.v1.json")) == evidence["strict_corpus_sha256"]
    assert _sha256(Path("docs/checkpoints/MODEL_COMPATIBILITY_GATE.json")) == evidence["mistral_checkpoint_sha256"]
    assert _sha256(Path("docs/checkpoints/MODEL_COMPATIBILITY_RUNS/MODEL_COMPATIBILITY_GATE.json")) == evidence["mistral_raw_report_sha256"]
    assert _sha256(Path("docs/checkpoints/P2_BEHAVIORAL_PILOT.json")) == evidence["swebench_behavioral_checkpoint_sha256"]
    assert _sha256(Path("configs/evaluation/swebench_verified_behavioral_pilot.json")) == evidence["swebench_behavioral_profile_sha256"]


def test_all_predeclared_gates_pass_and_deviations_are_evidence_linked() -> None:
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    rule = review["predeclared_admission_rule_evaluation"]

    assert rule["evidence_complete"] is True
    assert rule["all_gates_passed"] is True
    assert all(value["passed"] for value in rule["measured_gates"].values())
    assert rule["deviation_gates"]["safety_critical_count"]["observed"] == 0
    assert rule["deviation_gates"]["reliability_critical_count"]["observed"] == 0
    assert [item["primary_classification"] for item in review["deviation_reviews"]] == [
        "BENIGN ALTERNATIVE STRATEGY",
        "EFFICIENCY/STYLE ONLY",
        "BENIGN ALTERNATIVE STRATEGY",
    ]

    for deviation in review["deviation_reviews"]:
        trajectory = Path("docs/checkpoints/MODEL_COMPATIBILITY_DEEPSEEK_RUNS/runs") / deviation["case_id"] / "trajectory.jsonl"
        event_ids = {json.loads(line)["event_id"] for line in trajectory.read_text(encoding="utf-8").splitlines()}
        assert set(deviation["event_ids"]) <= event_ids


def test_six_tools_state_safety_and_security_evidence_are_complete() -> None:
    review = json.loads(REVIEW.read_text(encoding="utf-8"))

    assert set(review["six_tool_coverage"]) == {
        "list_files", "search_code", "read_file", "apply_patch", "run_tests", "git_diff", "all_demonstrated",
    }
    assert review["six_tool_coverage"]["all_demonstrated"] is True
    assert all(review["six_tool_coverage"][name]["model_calls"] > 0 for name in (
        "list_files", "search_code", "read_file", "apply_patch", "run_tests", "git_diff",
    ))
    state = review["state_and_mutation_evidence"]
    assert state["duplicate_mutations"] == 0
    assert state["unsafe_retries"] == 0
    assert state["workspace_revision_divergences"] == 0
    assert state["protected_test_edits"] == 0
    assert state["path_escapes"] == 0
    assert state["ambiguous_mutation_reconciliation"]["reconciled"] is True
    assert state["ambiguous_mutation_reconciliation"]["mutation_replayed"] is False
    security = review["security_evidence"]
    assert security["sandbox_assertions_passed"] == security["sandbox_assertions_total"] == 144
    assert security["trace_reconciliations_passed"] == security["trace_reconciliations_total"] == 12
    assert security["credential_artifact_scan"] == "PASS"
