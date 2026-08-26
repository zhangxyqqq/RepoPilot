from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from repopilot.trajectory.schema import TraceValidationError, normalize_event


class TraceReader:
    """Stream and validate a V1 or V2 append-only JSONL trajectory."""

    def __init__(self, path: Path, *, allow_trailing_partial: bool = False):
        self.path = path
        self.allow_trailing_partial = allow_trailing_partial
        self.complete = False
        self.issues: list[dict[str, Any]] = []

    def __iter__(self) -> Iterator[dict[str, Any]]:
        expected_sequence = 1
        run_id: str | None = None
        trace_id: str | None = None
        event_ids: set[str] = set()
        span_ids: set[str] = set()
        finished_seen = False
        self.complete = False
        self.issues = []

        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as exc:
                    remainder = handle.read()
                    if self.allow_trailing_partial and not remainder.strip():
                        self.issues.append(
                            {
                                "line": line_number,
                                "code": "trailing_partial_json",
                                "message": str(exc),
                            }
                        )
                        break
                    raise TraceValidationError(f"line {line_number}: malformed JSON: {exc}") from exc
                if not isinstance(raw, dict):
                    raise TraceValidationError(f"line {line_number}: trace event must be an object")
                try:
                    event = normalize_event(raw)
                except TraceValidationError as exc:
                    raise TraceValidationError(f"line {line_number}: {exc}") from exc
                if finished_seen:
                    raise TraceValidationError(f"line {line_number}: event appeared after run_finished")
                if event["sequence"] != expected_sequence:
                    raise TraceValidationError(
                        f"line {line_number}: expected sequence {expected_sequence}, got {event['sequence']}"
                    )
                if run_id is None:
                    run_id = event["run_id"]
                    trace_id = event["trace_id"]
                elif event["run_id"] != run_id or event["trace_id"] != trace_id:
                    raise TraceValidationError(f"line {line_number}: run/trace identifier changed")
                if event["event_id"] in event_ids:
                    raise TraceValidationError(f"line {line_number}: duplicate event_id")
                parent = event["parent_span_id"]
                if parent is not None and parent not in span_ids:
                    raise TraceValidationError(f"line {line_number}: parent span has not been observed")
                event_ids.add(event["event_id"])
                span_ids.add(event["span_id"])
                expected_sequence += 1
                if event["type"] == "run_finished":
                    self.complete = True
                    finished_seen = True
                yield event

        if not self.complete and not self.issues:
            self.issues.append(
                {"line": None, "code": "incomplete_trace", "message": "run_finished was not observed"}
            )
