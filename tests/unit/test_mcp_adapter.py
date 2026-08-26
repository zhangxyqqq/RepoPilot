from __future__ import annotations

import anyio
from functools import partial

from repopilot.mcp import McpToolAdapter, measure_adapter_overhead, normalize_tool_result
from repopilot.models import ToolResult
from repopilot.tools import PUBLIC_TOOL_DEFINITIONS, ToolRegistry


class _FixtureSandbox:
    def invoke(self, name, arguments):
        observations = {
            "list_files": {"files": ["app.py"], "count": 1, "truncated": False},
            "search_code": {"matches": [], "truncated": False},
            "read_file": {"path": "app.py", "content": "value = 1\n", "start_line": 1, "end_line": 1},
            "apply_patch": {"changed": True, "changed_files": ["app.py"]},
            "run_tests": {"passed": True, "exit_code": 0, "output": "1 passed", "timed_out": False},
            "git_diff": {"diff": "", "changed_files": []},
        }
        return {"ok": True, "result": observations[name], "latency_ms": 1.25}


class _FakeBackend:
    definitions = PUBLIC_TOOL_DEFINITIONS
    schemas = [definition.provider_schema() for definition in definitions]
    revision = 0
    unnecessary_calls = 0

    def call(self, name, arguments):
        return ToolResult(True, {"echo": arguments}, 0.0, 0)


ARGUMENTS = {
    "list_files": {},
    "search_code": {"query": "value"},
    "read_file": {"path": "app.py"},
    "apply_patch": {"patch": "patch"},
    "run_tests": {},
    "git_diff": {},
}


def test_mcp_catalog_has_exact_provider_schema_parity() -> None:
    registry = ToolRegistry(_FixtureSandbox())  # type: ignore[arg-type]
    mcp_tools = McpToolAdapter(registry).list_tools()

    assert len(mcp_tools) == len(registry.schemas) == 6
    for direct, mcp_tool, definition in zip(registry.schemas, mcp_tools, registry.definitions, strict=True):
        assert mcp_tool.name == direct["name"] == definition.name
        assert mcp_tool.description == direct["description"] == definition.description
        assert mcp_tool.inputSchema == direct["parameters"] == definition.input_schema
        assert mcp_tool.inputSchema.get("required", []) == direct["parameters"].get("required", [])
        assert mcp_tool.inputSchema["additionalProperties"] is direct["parameters"]["additionalProperties"]
    assert {definition.name for definition in registry.definitions if definition.public} == {
        "list_files", "search_code", "read_file", "apply_patch", "run_tests", "git_diff"
    }


def test_all_six_tools_have_direct_and_mcp_result_parity() -> None:
    async def compare() -> None:
        for name, arguments in ARGUMENTS.items():
            direct_registry = ToolRegistry(_FixtureSandbox())  # type: ignore[arg-type]
            mcp_registry = ToolRegistry(_FixtureSandbox())  # type: ignore[arg-type]
            direct = normalize_tool_result(direct_registry.call(name, arguments))
            transported = await McpToolAdapter(mcp_registry).call_tool(name, arguments)
            assert transported.structuredContent == direct
            assert transported.isError is False
    anyio.run(compare)


def test_direct_and_mcp_validation_error_semantics_match() -> None:
    async def compare() -> None:
        scenarios = [
            ("unknown", {}),
            ("search_code", {}),
            ("read_file", {"path": "app.py", "extra": True}),
            ("read_file", {"path": 42}),
        ]
        for name, arguments in scenarios:
            direct = normalize_tool_result(ToolRegistry(_FixtureSandbox()).call(name, arguments))  # type: ignore[arg-type]
            transported = await McpToolAdapter(ToolRegistry(_FixtureSandbox())).call_tool(name, arguments)  # type: ignore[arg-type]
            structured = dict(transported.structuredContent or {})
            assert structured.pop("latency_ms") >= 0
            assert direct.pop("latency_ms") >= 0
            assert structured == direct
            assert transported.isError is True
    anyio.run(compare)


def test_fake_backend_adapter_overhead_is_below_target() -> None:
    metrics = anyio.run(partial(measure_adapter_overhead, McpToolAdapter(_FakeBackend()), "git_diff", {}, calls=100))
    assert metrics["calls"] == 100
    assert metrics["median_ms"] <= 10
    assert metrics["p95_ms"] >= metrics["median_ms"]
    assert metrics["median_serialization_bytes"] > 0
