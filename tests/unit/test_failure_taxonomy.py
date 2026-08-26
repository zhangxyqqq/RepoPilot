from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from repopilot.evaluation.taxonomy import classify_failures, load_failure_taxonomy
from repopilot.trajectory import TraceReader


def _event(sequence: int, event_type: str, *, payload: dict[str, Any] | None = None, error: dict[str, Any] | None = None, revision: int = 0, status: str = "ok") -> dict[str, Any]:
    return {
        "sequence": sequence,
        "event_id": f"event-{sequence}",
        "type": event_type,
        "payload": payload or {},
        "error": error,
        "status": status,
        "workspace_revision_before": revision,
    }


def _inputs(scenario: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    error = lambda code, message: {"code": code, "message": message, "retryable": False, "stage": "tool"}
    evaluations: dict[str, dict[str, Any]] = {
        "retrieval_miss": {"expected_fix_files": ["src/a.py"], "retrieved_files": ["src/b.py"]},
        "overbroad": {"changed_files": ["src/a.py", "src/b.py"], "allowed_fix_sets": [["src/a.py"]]},
        "public_hidden": {"public_tests": {"passed": True}, "hidden_tests": {"passed": False}},
        "reference_integrity": {"reference_integrity_passed": False},
        "harness": {"harness_failed": True},
    }
    if scenario in evaluations:
        return [], {**evaluations[scenario], "evidence_event_id": "oracle-1"}
    if scenario == "staging": return [_event(1, "setup_error", payload={"setup_stage": "staging"}, error=error("execution_error", "stage"))], {}
    if scenario == "sandbox": return [_event(1, "setup_error", payload={"setup_stage": "sandbox_start"}, error=error("execution_error", "start"))], {}
    if scenario == "provider": return [_event(1, "model_error", error=error("model_error", "provider unavailable"))], {}
    if scenario == "malformed": return [_event(1, "model_error", error=error("malformed_output", "invalid json"))], {}
    if scenario == "invalid_action": return [_event(1, "model_turn", payload={"action": {"kind": "tool", "tool_name": None}})], {}
    if scenario == "premature_final": return [_event(1, "run_finished", payload={"stop_reason": "model_final", "success": False})], {}
    if scenario == "unknown_tool": return [_event(1, "tool_call", payload={"tool": "bad"}, error=error("unknown_tool", "unknown tool"))], {}
    if scenario == "invalid_arguments": return [_event(1, "tool_call", payload={"tool": "read_file"}, error=error("invalid_arguments", "invalid"))], {}
    if scenario == "duplicate": return [_event(1, "tool_call", payload={"tool": "read_file", "arguments": {"path": "a"}}), _event(2, "tool_call", payload={"tool": "read_file", "arguments": {"path": "a"}})], {}
    if scenario == "tool_timeout": return [_event(1, "tool_call", payload={"tool": "read_file"}, error=error("timeout", "timed out"), status="timeout")], {}
    if scenario == "tool_error": return [_event(1, "tool_call", payload={"tool": "read_file"}, error=error("tool_error", "failed"))], {}
    if scenario == "path_policy": return [_event(1, "tool_call", payload={"tool": "read_file"}, error=error("policy_rejected", "path must remain inside repository"))], {}
    if scenario == "test_policy": return [_event(1, "tool_call", payload={"tool": "apply_patch"}, error=error("policy_rejected", "protected test files are protected"))], {}
    if scenario == "command_policy": return [_event(1, "tool_call", payload={"tool": "run_tests"}, error=error("invalid_arguments", "command not allowed"))], {}
    if scenario == "retrieval_truncated": return [_event(1, "tool_call", payload={"tool": "list_files", "observation": {"repository_context": {"truncated": True}}})], {}
    if scenario == "patch_rejected": return [_event(1, "tool_call", payload={"tool": "apply_patch"}, error=error("tool_error", "patch failed"))], {}
    if scenario == "no_op": return [_event(1, "tool_call", payload={"tool": "apply_patch", "ok": True, "observation": {"changed": False}})], {}
    if scenario == "public_failed": return [_event(1, "tool_call", payload={"tool": "run_tests", "ok": True, "observation": {"passed": False}})], {}
    if scenario == "test_timeout": return [_event(1, "tool_call", payload={"tool": "run_tests", "observation": {"timed_out": True}}, error=error("timeout", "timed out"), status="timeout")], {}
    stop = {"iteration_limit": "iteration_limit", "repair_limit": "repair_limit", "total_timeout": "total_timeout"}[scenario]
    return [_event(1, "run_finished", payload={"stop_reason": stop, "success": False})], {}


def test_gold_taxonomy_rules_have_exact_deterministic_agreement() -> None:
    fixture = json.loads(Path("tests/fixtures/taxonomy/gold_labels.json").read_text())
    taxonomy = load_failure_taxonomy()
    covered: set[str] = set()
    for item in fixture:
        events, evaluation = _inputs(item["scenario"])
        first = classify_failures(events, evaluation=evaluation, taxonomy=taxonomy)
        second = classify_failures(events, evaluation=evaluation, taxonomy=taxonomy)
        actual = [classification["label"] for classification in first["classifications"]]
        assert actual == sorted(item["expected"]), item["scenario"]
        assert first == second
        covered.update(actual)
    assert covered == set(taxonomy.definitions)


def test_multiple_signals_and_evidence_are_preserved() -> None:
    events, evaluation = _inputs("test_policy")
    result = classify_failures(events, evaluation=evaluation)
    assert {item["label"] for item in result["classifications"]} == {
        "tool.execution_error", "policy.test_edit_rejected", "edit.patch_rejected"
    }
    assert all(item["evidence_event_ids"] == ["event-1"] for item in result["classifications"])


def test_historical_missing_evidence_is_unavailable_not_absent() -> None:
    result = classify_failures([])
    assert result["evidence_availability"] == "unavailable"
    assert result["historical_missing_evidence"] is True


def test_v1_trajectory_is_back_classified_with_missing_oracle_evidence_marked() -> None:
    result = classify_failures(TraceReader(Path("tests/fixtures/v1/trajectory.jsonl")))
    assert result["evidence_availability"] == "trace_only"
    assert result["historical_missing_evidence"] is True
    assert "policy.path_rejected" in {item["label"] for item in result["classifications"]}


def test_unknown_structured_error_is_explicitly_unclassified() -> None:
    event = _event(1, "controller_fault", error={"code": "new_error", "message": "new", "retryable": False, "stage": "controller"})
    result = classify_failures([event])
    assert result["unclassified_error_event_ids"] == ["event-1"]
    assert result["unclassified_error_count"] == 1


def test_harness_failure_is_not_mislabeled_as_agent_hidden_failure() -> None:
    result = classify_failures([], evaluation={
        "public_tests": {"passed": True},
        "hidden_tests": {"passed": False},
        "harness_failed": True,
        "evidence_event_id": "oracle-1",
    })
    assert [item["label"] for item in result["classifications"]] == ["evaluation.harness_failed"]


def test_recovery_decisions_distinguish_non_retryable_and_retryable_unrecovered() -> None:
    for retryable, expected in ((False, "non_retryable"), (True, "retryable_unrecovered")):
        events = [
            _event(1, "model_error", error={
                "code": "provider_error", "message": "provider failed", "retryable": retryable,
                "stage": "model", "injected": True,
            }),
            _event(2, "recovery_decision", payload={
                "evidence_event_ids": ["event-1"], "action": "stop", "retryable": retryable,
            }),
        ]
        result = classify_failures(events)
        classification = result["classifications"][0]
        assert classification["recoverability"] == expected
        assert classification["fault_origin"] == "injected"


def test_timeout_is_not_also_classified_as_public_test_failure() -> None:
    events, evaluation = _inputs("test_timeout")
    labels = {item["label"] for item in classify_failures(events, evaluation=evaluation)["classifications"]}
    assert labels == {"tool.execution_timeout", "test.timed_out"}
