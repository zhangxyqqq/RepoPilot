from datetime import datetime, timezone

import pytest

from repopilot.trajectory.schema import (
    REDACTED,
    TraceValidationError,
    create_event,
    normalize_event,
    redact_text,
    redact_value,
    validate_event,
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_v2_event_has_stable_envelope_and_parent_identifiers():
    model = create_event(
        run_id="run",
        sequence=2,
        timestamp=_timestamp(),
        event_type="model_turn",
        data={"iteration": 1, "latency_ms": 3.0, "token_usage": {}},
    )
    tool = create_event(
        run_id="run",
        sequence=3,
        timestamp=_timestamp(),
        event_type="tool_call",
        data={
            "iteration": 1,
            "origin": "model",
            "tool": "read_file",
            "ok": True,
            "workspace_revision_before": 0,
            "workspace_revision_after": 0,
        },
    )
    repeated = create_event(
        run_id="run",
        sequence=3,
        timestamp=tool["timestamp"],
        event_type="tool_call",
        data={
            "iteration": 1,
            "origin": "model",
            "tool": "read_file",
            "ok": True,
            "workspace_revision_before": 0,
            "workspace_revision_after": 0,
        },
    )

    assert tool["schema_version"] == 2
    assert tool["trace_id"] == model["trace_id"]
    assert tool["parent_span_id"] == model["span_id"]
    assert tool["event_id"] == repeated["event_id"]
    assert tool["span_id"] == repeated["span_id"]
    validate_event(tool)


def test_error_metadata_is_normalized_and_bounded():
    event = create_event(
        run_id="run",
        sequence=1,
        timestamp=_timestamp(),
        event_type="tool_call",
        data={"tool": "read_file", "ok": False, "error": "Docker command timed out: details"},
    )

    assert event["status"] == "timeout"
    assert event["error"] == {
        "code": "timeout",
        "stage": "tool",
        "retryable": True,
        "message": "Docker command timed out: details",
    }


def test_redaction_removes_credentials_but_preserves_token_counts():
    secret = "phase-one-secret-123"
    value = {
        "api_key": secret,
        "message": f"OPENAI_API_KEY={secret} Authorization: Bearer abcdefgh12345",
        "token_usage": {"input_tokens": 12, "output_tokens": 3},
    }

    redacted = redact_value(value)

    assert secret not in str(redacted)
    assert "abcdefgh12345" not in str(redacted)
    assert redacted["api_key"] == REDACTED
    assert redacted["token_usage"] == {"input_tokens": 12, "output_tokens": 3}
    assert redact_text("sk-abcdefghijk") == REDACTED


def test_unknown_schema_and_invalid_v2_fields_are_rejected():
    with pytest.raises(TraceValidationError, match="unsupported trace schema"):
        normalize_event({"schema_version": 99})

    event = create_event(
        run_id="run",
        sequence=1,
        timestamp=_timestamp(),
        event_type="run_started",
        data={},
    )
    event["status"] = "mystery"
    with pytest.raises(TraceValidationError, match="status"):
        validate_event(event)
