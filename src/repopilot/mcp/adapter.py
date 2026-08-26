from __future__ import annotations

import json
import re
import statistics
import time
from dataclasses import asdict
from typing import Any

import anyio
import mcp.types as types

from repopilot.models import ToolResult
from repopilot.tools import ToolBackend
from repopilot.trajectory import TrajectoryRecorder, redact_value
from repopilot.trajectory.schema import normalize_error


_HOST_PATH = re.compile(
    r"(?<![\w.])(?:(?:\.\.[/\\])+(?:Users|home|private|tmp|var|etc)|"
    r"(?:[A-Za-z]:\\|/)(?:Users|home|private|tmp|var|etc))(?:[/\\][^\s\"'<>|]*)?"
)


def sanitize_mcp_value(value: Any) -> Any:
    safe = redact_value(value)
    if isinstance(safe, str):
        return _HOST_PATH.sub("[REDACTED_PATH]", safe)
    if isinstance(safe, dict):
        return {str(key): sanitize_mcp_value(item) for key, item in safe.items()}
    if isinstance(safe, list):
        return [sanitize_mcp_value(item) for item in safe]
    return safe


def normalize_tool_result(result: ToolResult) -> dict[str, Any]:
    return sanitize_mcp_value(
        {
            "ok": result.ok,
            "observation": result.observation,
            "latency_ms": result.latency_ms,
            "revision": result.revision,
            "error": normalize_error(asdict(result.failure) if result.failure else result.error, phase="tool"),
        }
    )


class McpToolAdapter:
    def __init__(self, backend: ToolBackend, *, recorder: TrajectoryRecorder | None = None):
        self.backend = backend
        self.recorder = recorder

    def list_tools(self) -> list[types.Tool]:
        return [
            types.Tool(
                name=definition.name,
                description=definition.description,
                inputSchema=definition.input_schema,
                outputSchema=definition.result_schema(),
                annotations=types.ToolAnnotations(
                    readOnlyHint=definition.access == "read_only",
                    destructiveHint=definition.access == "mutating",
                ),
            )
            for definition in self.backend.definitions
            if definition.public
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> types.CallToolResult:
        revision_before = self.backend.revision
        result = await anyio.to_thread.run_sync(lambda: self.backend.call(name, arguments))
        normalized = normalize_tool_result(result)
        if self.recorder is not None:
            self.recorder.record(
                "tool_call",
                origin="mcp",
                transport="stdio",
                correlation_id=correlation_id,
                tool=name,
                arguments=sanitize_mcp_value(arguments),
                ok=result.ok,
                observation=sanitize_mcp_value(result.observation),
                error=sanitize_mcp_value(result.error),
                latency_ms=result.latency_ms,
                workspace_revision_before=revision_before,
                workspace_revision_after=result.revision,
            )
        text = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            structuredContent=normalized,
            isError=not result.ok,
        )


async def measure_adapter_overhead(
    adapter: McpToolAdapter,
    name: str,
    arguments: dict[str, Any],
    *,
    calls: int = 100,
) -> dict[str, float | int]:
    if calls < 2:
        raise ValueError("calls must be at least 2")
    samples: list[float] = []
    sizes: list[int] = []
    for _ in range(calls):
        started = time.perf_counter()
        result = await adapter.call_tool(name, arguments)
        samples.append((time.perf_counter() - started) * 1000)
        sizes.append(len(result.content[0].text.encode("utf-8")))
    ordered = sorted(samples)
    p95_index = min(len(ordered) - 1, int(0.95 * len(ordered)))
    return {
        "calls": calls,
        "median_ms": statistics.median(samples),
        "p95_ms": ordered[p95_index],
        "median_serialization_bytes": int(statistics.median(sizes)),
    }
