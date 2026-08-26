import json
from pathlib import Path

from repopilot.cli import main
from repopilot.trajectory import summarize_trace, write_trace_summary


V1_FIXTURE = Path("tests/fixtures/v1/trajectory.jsonl")


def test_v1_trace_analytics_reconstruct_metrics():
    summary = summarize_trace(V1_FIXTURE)

    assert summary["complete"] is True
    assert summary["source_schema_versions"] == [1]
    assert summary["tool_calls"]["total"] == 2
    assert summary["tool_calls"]["by_status"] == {"ok": 1, "rejected": 1}
    assert summary["latency_ms"] == {"model": 4.0, "tool": 4.5, "run": 10.0}
    assert summary["token_usage"] == {
        "input_tokens": 10,
        "output_tokens": 2,
        "cached_tokens": None,
        "reasoning_tokens": None,
    }
    assert summary["workspace_revisions"] == {"observed": [0], "final": 0, "transitions": 0}
    assert summary["final"] == {"status": "ok", "success": True, "stop_reason": "tests_passed"}


def test_trace_summary_artifacts_are_deterministic_and_redacted(tmp_path: Path):
    secret = "phase-one-secret-123"
    trace = tmp_path / "trajectory.jsonl"
    raw = V1_FIXTURE.read_text(encoding="utf-8").replace(
        "Fix target behavior",
        f"OPENAI_API_KEY={secret}",
    )
    trace.write_text(raw, encoding="utf-8")
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    first, first_json, first_markdown = write_trace_summary(trace, first_dir)
    second, second_json, second_markdown = write_trace_summary(trace, second_dir)

    assert first == second
    assert first_json.read_bytes() == second_json.read_bytes()
    assert first_markdown.read_bytes() == second_markdown.read_bytes()
    artifacts = trace.read_text() + first_json.read_text() + first_markdown.read_text()
    assert secret in trace.read_text()
    assert secret not in first_json.read_text()
    assert secret not in first_markdown.read_text()
    assert first["trace"]["run_id"] == "v1-fixture"


def test_trace_summary_cli_writes_json_and_markdown(tmp_path: Path):
    output = tmp_path / "summary"

    assert main(["trace-summary", str(V1_FIXTURE), "--output", str(output)]) == 0

    persisted = json.loads((output / "trace-summary.json").read_text(encoding="utf-8"))
    assert persisted["trace"]["tool_calls"]["total"] == 2
    assert "# RepoPilot trace summary" in (output / "trace-summary.md").read_text(encoding="utf-8")


def test_v1_run_artifact_reconciles_only_fields_it_contains(tmp_path: Path):
    trajectory = tmp_path / "trajectory.jsonl"
    trajectory.write_text(V1_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    run_path = tmp_path / "run.json"
    run_path.write_text(
        json.dumps(
            {
                "tool_calls": 2,
                "latency_ms": 10.0,
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "cached_tokens": None,
                    "reasoning_tokens": None,
                },
                "success": True,
                "stop_reason": "tests_passed",
            }
        ),
        encoding="utf-8",
    )

    artifact, _, _ = write_trace_summary(
        trajectory,
        tmp_path / "summary",
        run_path=run_path,
    )

    assert artifact["reconciliation"]["passed"] is True
    assert "model_latency_ms" not in artifact["reconciliation"]["checks"]


def test_trace_summary_cli_rejects_malformed_middle_event(tmp_path: Path):
    path = tmp_path / "trajectory.jsonl"
    source = V1_FIXTURE.read_text(encoding="utf-8").splitlines()
    path.write_text(source[0] + "\nnot-json\n" + source[1] + "\n", encoding="utf-8")

    assert main(["trace-summary", str(path), "--output", str(tmp_path / "out")]) == 2
    assert not (tmp_path / "out" / "trace-summary.json").exists()
