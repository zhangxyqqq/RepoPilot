from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from repopilot.trajectory.reader import TraceReader
from repopilot.trajectory.schema import redact_value


TOKEN_FIELDS = ("input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens")


def _add_usage(total: dict[str, int | None], usage: Mapping[str, Any]) -> None:
    for field in TOKEN_FIELDS:
        value = usage.get(field)
        if value is None:
            continue
        total[field] = (total[field] or 0) + int(value)


def summarize_events(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    phase_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    tool_names: Counter[str] = Counter()
    tool_statuses: Counter[str] = Counter()
    error_codes: Counter[str] = Counter()
    versions: set[int] = set()
    revisions: set[int] = set()
    revision_transitions = 0
    usage = {field: None for field in TOKEN_FIELDS}
    event_count = 0
    model_latency = 0.0
    tool_latency = 0.0
    run_latency: float | None = None
    run_id: str | None = None
    trace_id: str | None = None
    final_status: str | None = None
    final_success: bool | None = None
    stop_reason: str | None = None

    for event in events:
        event_count += 1
        run_id = event["run_id"]
        trace_id = event["trace_id"]
        versions.add(int(event["source_schema_version"]))
        phase_counts[event["phase"]] += 1
        status_counts[event["status"]] += 1
        duration = float(event["duration_ms"] or 0.0)
        if event["phase"] == "model":
            model_latency += duration
        if event["type"] == "model_turn":
            _add_usage(usage, event["payload"].get("token_usage", {}))
        elif event["type"] == "tool_call":
            tool_latency += duration
            tool_name = str(event["payload"].get("tool", "unknown"))
            tool_names[tool_name] += 1
            tool_statuses[event["status"]] += 1
        if event["error"] is not None:
            error_codes[str(event["error"]["code"])] += 1
        before = event.get("workspace_revision_before")
        after = event.get("workspace_revision_after")
        for revision in (before, after):
            if revision is not None:
                revisions.add(int(revision))
        if before is not None and after is not None and before != after:
            revision_transitions += 1
        if event["type"] == "run_finished":
            run_latency = duration
            final_status = event["status"]
            final_success = bool(event["payload"].get("success"))
            stop_reason_value = event["payload"].get("stop_reason")
            stop_reason = str(stop_reason_value) if stop_reason_value is not None else None

    ordered_revisions = sorted(revisions)
    return {
        "summary_schema_version": 1,
        "run_id": run_id,
        "trace_id": trace_id,
        "source_schema_versions": sorted(versions),
        "event_count": event_count,
        "phase_counts": dict(sorted(phase_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "tool_calls": {
            "total": sum(tool_names.values()),
            "by_name": dict(sorted(tool_names.items())),
            "by_status": dict(sorted(tool_statuses.items())),
        },
        "latency_ms": {
            "model": model_latency,
            "tool": tool_latency,
            "run": run_latency,
        },
        "token_usage": usage,
        "workspace_revisions": {
            "observed": ordered_revisions,
            "final": ordered_revisions[-1] if ordered_revisions else None,
            "transitions": revision_transitions,
        },
        "final": {
            "status": final_status,
            "success": final_success,
            "stop_reason": stop_reason,
        },
        "errors": {
            "total": sum(error_codes.values()),
            "by_code": dict(sorted(error_codes.items())),
        },
    }


def summarize_trace(path: Path, *, allow_trailing_partial: bool = True) -> dict[str, Any]:
    reader = TraceReader(path, allow_trailing_partial=allow_trailing_partial)
    summary = summarize_events(reader)
    summary["complete"] = reader.complete
    summary["reader_issues"] = reader.issues
    return summary


def reconcile_summary(summary: Mapping[str, Any], run: Mapping[str, Any]) -> dict[str, Any]:
    stored_trace = run.get("trace_summary")
    checks: dict[str, dict[str, Any]] = {}

    def compare(name: str, actual: Any, expected: Any) -> None:
        checks[name] = {"actual": actual, "expected": expected, "match": actual == expected}

    compare("tool_count", summary["tool_calls"]["total"], run.get("tool_calls"))
    compare("run_latency_ms", summary["latency_ms"]["run"], run.get("latency_ms"))
    compare("token_usage", summary["token_usage"], run.get("usage"))
    compare("final_status", summary["final"]["status"], "ok" if run.get("success") else "error")
    compare("stop_reason", summary["final"]["stop_reason"], run.get("stop_reason"))
    if isinstance(stored_trace, Mapping):
        for name, path in (
            ("model_latency_ms", ("latency_ms", "model")),
            ("tool_latency_ms", ("latency_ms", "tool")),
            ("workspace_revisions", ("workspace_revisions",)),
        ):
            expected: Any = stored_trace
            for key in path:
                expected = expected.get(key) if isinstance(expected, Mapping) else None
            actual: Any = summary
            for key in path:
                actual = actual.get(key) if isinstance(actual, Mapping) else None
            compare(name, actual, expected)
    return {"available": True, "passed": all(item["match"] for item in checks.values()), "checks": checks}


def _markdown(summary: Mapping[str, Any], reconciliation: Mapping[str, Any] | None) -> str:
    final = summary["final"]
    latency = summary["latency_ms"]
    usage = summary["token_usage"]
    lines = [
        "# RepoPilot trace summary",
        "",
        f"- Run ID: `{summary['run_id']}`",
        f"- Complete: {'yes' if summary['complete'] else 'no'}",
        f"- Events: {summary['event_count']}",
        f"- Tool calls: {summary['tool_calls']['total']}",
        f"- Final status: {final['status']}",
        f"- Stop reason: {final['stop_reason']}",
        f"- Model latency: {latency['model']:.3f} ms",
        f"- Tool latency: {latency['tool']:.3f} ms",
        f"- Run latency: {latency['run'] if latency['run'] is not None else 'unavailable'} ms",
        f"- Input/output tokens: {usage['input_tokens']}/{usage['output_tokens']}",
        f"- Final workspace revision: {summary['workspace_revisions']['final']}",
        f"- Errors: {summary['errors']['total']}",
    ]
    if reconciliation is not None:
        lines.append(f"- Run artifact reconciliation: {'PASS' if reconciliation['passed'] else 'FAIL'}")
    if summary["reader_issues"]:
        lines.extend(["", "## Reader issues", ""])
        lines.extend(
            f"- `{item['code']}` at line {item['line']}: {item['message']}"
            for item in summary["reader_issues"]
        )
    lines.append("")
    return "\n".join(lines)


def write_trace_summary(
    trajectory_path: Path,
    output_directory: Path,
    *,
    run_path: Path | None = None,
) -> tuple[dict[str, Any], Path, Path]:
    summary = summarize_trace(trajectory_path)
    reconciliation: dict[str, Any] | None = None
    if run_path is not None and run_path.exists():
        run = json.loads(run_path.read_text(encoding="utf-8"))
        reconciliation = reconcile_summary(summary, run)
    artifact = redact_value({"trace": summary, "reconciliation": reconciliation})
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "trace-summary.json"
    markdown_path = output_directory / "trace-summary.md"
    json_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(summary, reconciliation), encoding="utf-8")
    return artifact, json_path, markdown_path
