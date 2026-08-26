import json
from pathlib import Path

from repopilot.trajectory import TrajectoryRecorder


def test_trajectory_is_append_only_jsonl(tmp_path: Path):
    path = tmp_path / "trajectory.jsonl"
    recorder = TrajectoryRecorder(path, run_id="run-1", metadata={"model": "fake"})
    recorder.record("tool_call", tool="list_files", arguments={}, observation={"files": []})

    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [event["sequence"] for event in events] == [1, 2]
    assert [event["type"] for event in events] == ["run_started", "tool_call"]
    assert all(event["run_id"] == "run-1" for event in events)
    assert all(event["schema_version"] == 2 for event in events)
    assert len({event["event_id"] for event in events}) == 2
    assert all(event["trace_id"] == events[0]["trace_id"] for event in events)


def test_trajectory_redacts_credentials_before_appending(tmp_path: Path):
    secret = "phase-one-secret-123"
    path = tmp_path / "trajectory.jsonl"
    recorder = TrajectoryRecorder(
        path,
        run_id="run-1",
        metadata={"api_key": secret, "message": f"OPENAI_API_KEY={secret}"},
    )
    recorder.record("model_error", error=f"Authorization: Bearer {secret}")

    artifact = path.read_text(encoding="utf-8")
    assert secret not in artifact
    assert artifact.count("[REDACTED]") >= 3
