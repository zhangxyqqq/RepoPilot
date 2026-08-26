from __future__ import annotations

import json
from pathlib import Path

import pytest

from repopilot.evaluation.faults import FaultRuntime, FaultScheduleError, load_fault_schedule
from repopilot.trajectory import TraceReader, TrajectoryRecorder


SCHEDULE = Path("configs/faults/reliability_matrix.v1.json")


def test_canonical_schedule_is_versioned_data_only_and_covers_matrix() -> None:
    schedule = load_fault_schedule(SCHEDULE)
    assert schedule.version == 1
    assert len(schedule.scenarios) == 8
    assert len({scenario.scenario_id for scenario in schedule.scenarios}) == 8
    assert schedule.content_hash.startswith("sha256:")
    artifact = SCHEDULE.read_text(encoding="utf-8")
    for forbidden in ("command", "callable", "import_path", "mount", "network", "capabilities"):
        assert f'"{forbidden}"' not in artifact


def test_fault_schedule_reprocessing_is_deterministic() -> None:
    assert load_fault_schedule(SCHEDULE) == load_fault_schedule(SCHEDULE)


def test_same_schedule_calls_emit_same_injected_event_sequence(tmp_path: Path) -> None:
    scenario = load_fault_schedule(SCHEDULE).scenarios[-1]
    normalized_runs = []
    for name in ("first", "second"):
        path = tmp_path / f"{name}.jsonl"
        runtime = FaultRuntime(scenario, TrajectoryRecorder(path, run_id="stable-run", metadata={}))
        assert runtime.take_model() is not None
        assert runtime.take_controller("before_recovery") is not None
        events = list(TraceReader(path))
        normalized_runs.append([
            {
                "event_id": event["event_id"],
                "sequence": event["sequence"],
                "type": event["type"],
                "payload": event["payload"],
            }
            for event in events
        ])
        assert runtime.injected_sequence == ["transient_provider_exception", "near_deadline"]
    assert normalized_runs[0] == normalized_runs[1]


def test_unknown_or_executable_schedule_fields_fail_closed(tmp_path: Path) -> None:
    raw = json.loads(SCHEDULE.read_text(encoding="utf-8"))
    raw["scenarios"][0]["faults"][0]["command"] = "sh"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(FaultScheduleError):
        load_fault_schedule(path)


def test_unknown_tool_and_duplicate_fields_fail_closed(tmp_path: Path) -> None:
    raw = json.loads(SCHEDULE.read_text(encoding="utf-8"))
    raw["scenarios"][2]["faults"][0]["tool"] = "shell"
    path = tmp_path / "unknown.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(FaultScheduleError, match="unknown tool"):
        load_fault_schedule(path)
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(FaultScheduleError, match="duplicate"):
        load_fault_schedule(duplicate)
