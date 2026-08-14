import json
from pathlib import Path

import pytest

from repopilot.evaluation import evaluate_benchmarks


@pytest.mark.docker
def test_all_scripted_benchmarks_pass_and_emit_reports(tmp_path: Path):
    report = evaluate_benchmarks(Path("benchmarks/cases"), tmp_path / "reports")

    assert report["aggregate"]["cases"] == 4
    assert report["aggregate"]["tasks_succeeded"] == 4
    assert report["aggregate"]["success_rate"] == 1.0
    assert report["aggregate"]["localization_f1_mean"] == 1.0

    json_report = Path(report["report_paths"]["json"])
    markdown_report = Path(report["report_paths"]["markdown"])
    assert json_report.exists()
    assert markdown_report.exists()
    assert "# RepoPilot evaluation" in markdown_report.read_text(encoding="utf-8")

    persisted = json.loads(json_report.read_text(encoding="utf-8"))
    assert len(persisted["cases"]) == 4
    for case in persisted["cases"]:
        assert case["task_success"] is True
        assert case["changed_files"] == case["expected_fix_files"]
        events = [json.loads(line) for line in Path(case["trajectory_path"]).read_text(encoding="utf-8").splitlines()]
        event_types = {event["type"] for event in events}
        assert {"run_started", "model_turn", "tool_call", "run_finished"} <= event_types
