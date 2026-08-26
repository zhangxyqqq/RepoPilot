import json
from pathlib import Path

import pytest

from repopilot.trajectory import TraceReader, TraceValidationError, TrajectoryRecorder


V1_FIXTURE = Path("tests/fixtures/v1/trajectory.jsonl")


def test_v1_fixture_streams_as_normalized_v2_events():
    reader = TraceReader(V1_FIXTURE)

    events = list(reader)

    assert reader.complete is True
    assert reader.issues == []
    assert [event["sequence"] for event in events] == [1, 2, 3, 4, 5, 6]
    assert {event["schema_version"] for event in events} == {2}
    assert {event["source_schema_version"] for event in events} == {1}
    assert events[2]["parent_span_id"] == events[1]["span_id"]
    assert events[4]["status"] == "rejected"


def test_valid_trace_without_run_finished_is_reported_incomplete(tmp_path: Path):
    path = tmp_path / "trajectory.jsonl"
    recorder = TrajectoryRecorder(path, run_id="partial", metadata={})
    recorder.record("model_turn", iteration=1, latency_ms=1.0, token_usage={})
    reader = TraceReader(path)

    assert len(list(reader)) == 2
    assert reader.complete is False
    assert reader.issues[0]["code"] == "incomplete_trace"


def test_trailing_partial_json_can_be_read_but_middle_malformed_json_fails(tmp_path: Path):
    source = V1_FIXTURE.read_text(encoding="utf-8").splitlines()
    trailing = tmp_path / "trailing.jsonl"
    trailing.write_text("\n".join(source[:2]) + "\n{\"broken\":", encoding="utf-8")
    reader = TraceReader(trailing, allow_trailing_partial=True)

    assert len(list(reader)) == 2
    assert reader.complete is False
    assert reader.issues[0]["code"] == "trailing_partial_json"

    middle = tmp_path / "middle.jsonl"
    middle.write_text(source[0] + "\nnot-json\n" + source[1] + "\n", encoding="utf-8")
    with pytest.raises(TraceValidationError, match="malformed JSON"):
        list(TraceReader(middle, allow_trailing_partial=True))


def test_sequence_and_parent_integrity_are_validated(tmp_path: Path):
    lines = [json.loads(line) for line in V1_FIXTURE.read_text(encoding="utf-8").splitlines()]
    lines[1]["sequence"] = 4
    out_of_order = tmp_path / "out-of-order.jsonl"
    out_of_order.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    with pytest.raises(TraceValidationError, match="expected sequence"):
        list(TraceReader(out_of_order))

    path = tmp_path / "missing-parent.jsonl"
    recorder = TrajectoryRecorder(path, run_id="parent", metadata={})
    recorder.record(
        "tool_call",
        iteration=1,
        origin="model",
        tool="read_file",
        ok=True,
        workspace_revision_before=0,
        workspace_revision_after=0,
    )
    with pytest.raises(TraceValidationError, match="parent span"):
        list(TraceReader(path))
