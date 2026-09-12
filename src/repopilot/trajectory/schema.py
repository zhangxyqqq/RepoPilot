from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
import uuid
from datetime import datetime
from typing import Any, Mapping


TRACE_SCHEMA_VERSION = 2
TRACE_PHASES = frozenset({"run", "model", "tool", "controller", "evaluation"})
TRACE_STATUSES = frozenset({"in_progress", "ok", "error", "timeout", "rejected", "cancelled"})
REDACTED = "[REDACTED]"
_REDACTION_SECRETS: ContextVar[tuple[str, ...]] = ContextVar("repopilot_redaction_secrets", default=())


@contextmanager
def secret_redaction(secrets):
    """Optional control-plane literals; scoped to one execution, never recorded."""
    token = _REDACTION_SECRETS.set(tuple(sorted({s for s in secrets if s}, key=len, reverse=True)))
    try:
        yield
    finally:
        _REDACTION_SECRETS.reset(token)


_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "client_secret",
        "credential",
        "credentials",
        "password",
        "passwd",
        "private_key",
        "secret",
        "token",
        "access_token",
        "refresh_token",
    }
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r"(?i)\b([A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN|CLIENT_SECRET|PASSWORD))"
        r"(\s*=\s*)([^\s,;]+)"
    ),
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|client[_-]?secret|password|passwd|secret)"
        r"(\s*[:=]\s*)([^\s,;]+)"
    ),
    re.compile(r"(?i)(https?://[^\s/:@]+:)([^\s/@]+)(@)"),
)


class TraceValidationError(ValueError):
    """Raised when a trace event does not satisfy a supported schema."""


def stable_trace_id(run_id: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"repopilot:{run_id}:trace").hex


def stable_span_id(run_id: str, value: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"repopilot:{run_id}:span:{value}").hex[:16]


def stable_event_id(run_id: str, sequence: int, event_type: str) -> str:
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"repopilot:{run_id}:event:{sequence}:{event_type}",
    ).hex


def redact_text(value: str) -> str:
    redacted = value
    for secret in _REDACTION_SECRETS.get():
        redacted = redacted.replace(secret, REDACTED)
    for pattern in _SECRET_PATTERNS:
        if pattern.groups == 1:
            redacted = pattern.sub(r"\1 " + REDACTED, redacted)
        elif pattern.groups == 3:
            if pattern.pattern.startswith("(?i)(https?"):
                redacted = pattern.sub(r"\1" + REDACTED + r"\3", redacted)
            else:
                redacted = pattern.sub(r"\1\2" + REDACTED, redacted)
        else:
            redacted = pattern.sub(REDACTED, redacted)
    return redacted


def redact_value(value: Any, *, key: str | None = None) -> Any:
    normalized_key = (key or "").lower().replace("-", "_")
    if normalized_key in _SENSITIVE_KEYS:
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {redact_text(str(item_key)): redact_value(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    return value


def _phase_for(event_type: str) -> str:
    if event_type.startswith("model_"):
        return "model"
    if event_type == "tool_call" or event_type.startswith("tool_"):
        return "tool"
    if event_type.startswith("evaluation_"):
        return "evaluation"
    if event_type in {"run_started", "run_finished"}:
        return "run"
    return "controller"


def _error_code(message: str, *, phase: str) -> tuple[str, bool]:
    lowered = message.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout", True
    if "invalid tool arguments" in lowered:
        return "invalid_arguments", False
    if "unknown tool" in lowered:
        return "unknown_tool", False
    if any(term in lowered for term in ("denied", "not allowlisted", "protected", "must remain inside")):
        return "policy_rejected", False
    if phase == "model":
        return "model_error", False
    if phase == "tool":
        return "tool_error", False
    return "execution_error", False


def normalize_error(error: Any, *, phase: str) -> dict[str, Any] | None:
    if error is None or error == "":
        return None
    if isinstance(error, Mapping):
        message = redact_text(str(error.get("message", "unknown error")))
        inferred_code, inferred_retryable = _error_code(message, phase=phase)
        normalized = {
            "code": str(error.get("code") or inferred_code),
            "stage": str(error.get("stage") or phase),
            "retryable": bool(error.get("retryable", inferred_retryable)),
            "message": message[:2_000],
        }
        for field in ("execution_state", "operation_class", "injected", "fault_type"):
            if field in error and error[field] is not None:
                normalized[field] = error[field]
        return normalized
    message = redact_text(str(error))[:2_000]
    code, retryable = _error_code(message, phase=phase)
    return {"code": code, "stage": phase, "retryable": retryable, "message": message}


def _status_for(event_type: str, payload: Mapping[str, Any], error: dict[str, Any] | None) -> str:
    explicit = payload.get("status")
    if explicit in TRACE_STATUSES:
        return str(explicit)
    if event_type == "run_started":
        return "in_progress"
    if event_type == "run_finished":
        return "ok" if payload.get("success") else "error"
    if error is not None:
        if error["code"] == "timeout":
            return "timeout"
        if error["code"] in {"invalid_arguments", "unknown_tool", "policy_rejected"}:
            return "rejected"
        return "error"
    if event_type == "tool_call" and payload.get("ok") is False:
        return "error"
    return "ok"


def _span_ids(
    run_id: str,
    event_type: str,
    sequence: int,
    iteration: int | None,
    origin: str | None,
) -> tuple[str, str | None]:
    root = stable_span_id(run_id, "run")
    if event_type in {"run_started", "run_finished"}:
        return root, None
    if event_type == "model_turn" and iteration is not None:
        return stable_span_id(run_id, f"model:{iteration}"), root
    if event_type == "tool_call":
        span = stable_span_id(run_id, f"tool:{sequence}")
        if iteration is not None and origin == "model":
            return span, stable_span_id(run_id, f"model:{iteration}")
        return span, root
    return stable_span_id(run_id, f"{event_type}:{sequence}"), root


def create_event(
    *,
    run_id: str,
    sequence: int,
    timestamp: str,
    event_type: str,
    data: Mapping[str, Any],
    source_schema_version: int = TRACE_SCHEMA_VERSION,
) -> dict[str, Any]:
    safe_data = redact_value(dict(data))
    phase = str(safe_data.pop("phase", _phase_for(event_type)))
    iteration_value = safe_data.pop("iteration", None)
    iteration = int(iteration_value) if iteration_value is not None else None
    duration_value = safe_data.pop("duration_ms", safe_data.pop("latency_ms", None))
    duration_ms = float(duration_value) if duration_value is not None else None
    revision = safe_data.pop("workspace_revision", None)
    revision_before = safe_data.pop("workspace_revision_before", revision)
    revision_after = safe_data.pop("workspace_revision_after", revision)
    raw_error = safe_data.pop("error", None)
    normalized_error = normalize_error(raw_error, phase=phase)
    origin_value = safe_data.get("origin")
    origin = str(origin_value) if origin_value is not None else None
    span_id, parent_span_id = _span_ids(run_id, event_type, sequence, iteration, origin)
    status = _status_for(event_type, safe_data, normalized_error)
    safe_data.pop("status", None)
    event = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "source_schema_version": source_schema_version,
        "run_id": run_id,
        "trace_id": stable_trace_id(run_id),
        "event_id": stable_event_id(run_id, sequence, event_type),
        "sequence": sequence,
        "timestamp": timestamp,
        "type": event_type,
        "phase": phase,
        "status": status,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "iteration": iteration,
        "workspace_revision_before": revision_before,
        "workspace_revision_after": revision_after,
        "duration_ms": duration_ms,
        "payload": safe_data,
        "error": normalized_error,
    }
    validate_event(event)
    return event


def normalize_event(raw_event: Mapping[str, Any]) -> dict[str, Any]:
    version = raw_event.get("schema_version")
    if version == TRACE_SCHEMA_VERSION:
        event = redact_value(dict(raw_event))
        validate_event(event)
        return event
    if version != 1:
        raise TraceValidationError(f"unsupported trace schema version: {version!r}")
    required = {"run_id", "sequence", "timestamp", "type"}
    missing = sorted(required - set(raw_event))
    if missing:
        raise TraceValidationError(f"V1 event is missing required field: {missing[0]}")
    data = {
        key: value
        for key, value in raw_event.items()
        if key not in {"schema_version", "run_id", "sequence", "timestamp", "type"}
    }
    return create_event(
        run_id=str(raw_event["run_id"]),
        sequence=int(raw_event["sequence"]),
        timestamp=str(raw_event["timestamp"]),
        event_type=str(raw_event["type"]),
        data=data,
        source_schema_version=1,
    )


def validate_event(event: Mapping[str, Any]) -> None:
    required_types: dict[str, type | tuple[type, ...]] = {
        "schema_version": int,
        "source_schema_version": int,
        "run_id": str,
        "trace_id": str,
        "event_id": str,
        "sequence": int,
        "timestamp": str,
        "type": str,
        "phase": str,
        "status": str,
        "span_id": str,
        "payload": dict,
    }
    for field, expected_type in required_types.items():
        if field not in event:
            raise TraceValidationError(f"V2 event is missing required field: {field}")
        if not isinstance(event[field], expected_type):
            raise TraceValidationError(f"V2 event field {field} has the wrong type")
    if event["schema_version"] != TRACE_SCHEMA_VERSION:
        raise TraceValidationError(f"unsupported trace schema version: {event['schema_version']!r}")
    if event["source_schema_version"] not in {1, TRACE_SCHEMA_VERSION}:
        raise TraceValidationError("unsupported source trace schema version")
    if event["sequence"] < 1:
        raise TraceValidationError("trace sequence must be positive")
    if event["phase"] not in TRACE_PHASES:
        raise TraceValidationError(f"unsupported trace phase: {event['phase']!r}")
    if event["status"] not in TRACE_STATUSES:
        raise TraceValidationError(f"unsupported trace status: {event['status']!r}")
    if event.get("parent_span_id") is not None and not isinstance(event["parent_span_id"], str):
        raise TraceValidationError("parent_span_id must be a string or null")
    if event.get("iteration") is not None and not isinstance(event["iteration"], int):
        raise TraceValidationError("iteration must be an integer or null")
    for field in ("workspace_revision_before", "workspace_revision_after"):
        value = event.get(field)
        if value is not None and (not isinstance(value, int) or value < 0):
            raise TraceValidationError(f"{field} must be a non-negative integer or null")
    duration = event.get("duration_ms")
    if duration is not None and (not isinstance(duration, (int, float)) or duration < 0):
        raise TraceValidationError("duration_ms must be a non-negative number or null")
    error = event.get("error")
    if error is not None:
        if not isinstance(error, dict):
            raise TraceValidationError("error must be an object or null")
        for field in ("code", "stage", "retryable", "message"):
            if field not in error:
                raise TraceValidationError(f"trace error is missing required field: {field}")
        if not isinstance(error["retryable"], bool):
            raise TraceValidationError("trace error retryable must be boolean")
        if error.get("execution_state") is not None and error["execution_state"] not in {
            "pre_execution", "post_execution", "ambiguous_execution", "not_applicable",
        }:
            raise TraceValidationError("unsupported error execution_state")
        if error.get("operation_class") is not None and error["operation_class"] not in {
            "model", "safe_read", "bounded_test", "non_idempotent_mutation", "policy",
        }:
            raise TraceValidationError("unsupported error operation_class")
        if error.get("injected") is not None and not isinstance(error["injected"], bool):
            raise TraceValidationError("error injected must be boolean")
    try:
        datetime.fromisoformat(str(event["timestamp"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise TraceValidationError("timestamp must be ISO-8601") from exc
