import json
from pathlib import Path

import pytest

from repopilot.evaluation import evaluate_benchmarks


@pytest.mark.docker
def test_all_scripted_benchmarks_pass_and_emit_reports(tmp_path: Path):
    report = evaluate_benchmarks(Path("benchmarks/cases"), tmp_path / "reports")

    assert report["aggregate"]["cases"] == 12
    assert report["aggregate"]["tasks_succeeded"] == 12
    assert report["aggregate"]["success_rate"] == 1.0
    assert report["aggregate"]["public_test_cases_passed"] == 12
    assert report["aggregate"]["hidden_test_cases_passed"] == 12
    assert report["aggregate"]["localization_f1_mean"] == 1.0
    assert report["aggregate"]["failure_analysis"]["unclassified_error_count"] == 0
    assert report["aggregate"]["failure_analysis"]["failure_incidence_by_label"] == {}
    assert report["profile_acceptance"]["passed"] is True
    assert report["evaluation_track"] == "controlled_deterministic_infrastructure"

    json_report = Path(report["report_paths"]["json"])
    markdown_report = Path(report["report_paths"]["markdown"])
    assert json_report.exists()
    assert markdown_report.exists()
    assert "# RepoPilot deterministic evaluation" in markdown_report.read_text(encoding="utf-8")

    persisted = json.loads(json_report.read_text(encoding="utf-8"))
    field_fixture = json.loads(Path("tests/fixtures/v1/report_fields.json").read_text(encoding="utf-8"))
    assert set(field_fixture["evaluation_required_fields"]) <= set(persisted)
    assert set(field_fixture["aggregate_required_fields"]) <= set(persisted["aggregate"])
    assert len(persisted["cases"]) == 12
    for case in persisted["cases"]:
        assert set(field_fixture["case_required_fields"]) <= set(case)
        assert case["task_success"] is True
        assert case["public_tests"]["passed"] is True
        assert case["hidden_tests"]["passed"] is True
        assert case["changed_files"] == case["expected_fix_files"]
        assert case["stop_reason"] == "tests_passed"
        assert case["trace_reconciliation"]["passed"] is True
        assert case["trace_summary"]["tool_calls"]["total"] == case["tool_calls"]
        assert case["failure_analysis"]["terminal_reason"]["classification"] == "non_failure"
        assert case["failure_analysis"]["unclassified_error_count"] == 0
        assert case["evaluation_profile"] == persisted["profile"]
        run_artifact = json.loads(Path(case["trajectory_path"]).with_name("run.json").read_text())
        assert run_artifact["evaluation_profile"] == persisted["profile"]
        events = [json.loads(line) for line in Path(case["trajectory_path"]).read_text(encoding="utf-8").splitlines()]
        event_types = {event["type"] for event in events}
        assert {"run_started", "model_turn", "tool_call", "run_finished"} <= event_types
        assert {event["schema_version"] for event in events} == {2}
        assert len({event["event_id"] for event in events}) == len(events)
        assert events[0]["payload"]["metadata"]["evaluation_profile"] == persisted["profile"]
