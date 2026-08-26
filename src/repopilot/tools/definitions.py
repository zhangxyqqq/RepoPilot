from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


ToolAccess = Literal["read_only", "mutating"]
ToolIdempotency = Literal["safe_read", "bounded_test", "non_idempotent_mutation"]


@dataclass(frozen=True)
class ToolDefinition:
    """Transport-neutral public tool contract."""

    name: str
    description: str
    input_schema: dict[str, Any]
    access: ToolAccess
    idempotency: ToolIdempotency = "safe_read"
    public: bool = True
    result_fields: tuple[str, ...] = ("ok", "observation", "latency_ms", "revision", "error")
    error_codes: tuple[str, ...] = (
        "unknown_tool", "invalid_arguments", "policy_rejected", "timeout", "tool_error",
    )

    def provider_schema(self) -> dict[str, Any]:
        return {
            "type": "function", "name": self.name, "description": self.description,
            "parameters": self.input_schema, "strict": False,
        }

    def mcp_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "outputSchema": self.result_schema(),
        }

    def result_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "observation": {"type": "object"},
                "latency_ms": {"type": "number", "minimum": 0},
                "revision": {"type": "integer", "minimum": 0},
                "error": {
                    "anyOf": [
                        {"type": "null"},
                        {
                            "type": "object",
                            "properties": {
                                "code": {"type": "string"},
                                "stage": {"type": "string"},
                                "retryable": {"type": "boolean"},
                                "message": {"type": "string"},
                                "execution_state": {"type": "string"},
                                "operation_class": {"type": "string"},
                                "injected": {"type": "boolean"},
                                "fault_type": {"type": ["string", "null"]},
                            },
                            "required": ["code", "stage", "retryable", "message"],
                            "additionalProperties": False,
                        },
                    ]
                },
            },
            "required": list(self.result_fields),
            "additionalProperties": False,
        }


PUBLIC_TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        "list_files",
        "List files and return a bounded Python AST repo map with modules, imports, symbols, signatures, and lines.",
        {"type": "object", "properties": {"path": {"type": "string", "default": "."}}, "additionalProperties": False},
        "read_only",
    ),
    ToolDefinition(
        "search_code",
        "Search repository text using a literal query or bounded regular expression.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"}, "path": {"type": "string", "default": "."},
                "regex": {"type": "boolean", "default": False},
            },
            "required": ["query"], "additionalProperties": False,
        },
        "read_only",
    ),
    ToolDefinition(
        "read_file",
        "Read a bounded line range from a repository file.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1, "default": 1},
                "end_line": {"type": "integer", "minimum": 1, "default": 400},
            },
            "required": ["path"], "additionalProperties": False,
        },
        "read_only",
    ),
    ToolDefinition(
        "apply_patch",
        "Apply a Git diff or *** Begin Patch envelope to production files. Public test files are protected.",
        {"type": "object", "properties": {"patch": {"type": "string"}}, "required": ["patch"], "additionalProperties": False},
        "mutating",
        "non_idempotent_mutation",
    ),
    ToolDefinition(
        "run_tests",
        "Run the fixed, allowlisted pytest command for this repository.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        "read_only",
        "bounded_test",
    ),
    ToolDefinition(
        "git_diff",
        "Inspect the current repository diff and changed-file list.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        "read_only",
    ),
)

PUBLIC_TOOL_BY_NAME = {definition.name: definition for definition in PUBLIC_TOOL_DEFINITIONS}


def provider_tool_schemas() -> list[dict[str, Any]]:
    return [definition.provider_schema() for definition in PUBLIC_TOOL_DEFINITIONS if definition.public]
