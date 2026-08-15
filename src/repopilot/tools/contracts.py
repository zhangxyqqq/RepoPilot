from __future__ import annotations

from typing import Any


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "list_files",
        "description": "List files and return a bounded Python AST repo map with modules, imports, symbols, signatures, and lines.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "default": "."}},
            "additionalProperties": False,
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "search_code",
        "description": "Search repository text using a literal query or bounded regular expression.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path": {"type": "string", "default": "."},
                "regex": {"type": "boolean", "default": False},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "read_file",
        "description": "Read a bounded line range from a repository file.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1, "default": 1},
                "end_line": {"type": "integer", "minimum": 1, "default": 400},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "apply_patch",
        "description": "Apply a Git diff or *** Begin Patch envelope to production files. Public test files are protected.",
        "parameters": {
            "type": "object",
            "properties": {"patch": {"type": "string"}},
            "required": ["patch"],
            "additionalProperties": False,
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "run_tests",
        "description": "Run the fixed, allowlisted pytest command for this repository.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        "strict": False,
    },
    {
        "type": "function",
        "name": "git_diff",
        "description": "Inspect the current repository diff and changed-file list.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        "strict": False,
    },
]


def validate_tool_arguments(name: str, arguments: dict[str, Any]) -> str | None:
    schema = next((item for item in TOOL_SCHEMAS if item["name"] == name), None)
    if schema is None:
        return f"unknown tool: {name}"
    parameters = schema["parameters"]
    properties = parameters.get("properties", {})
    for required in parameters.get("required", []):
        if required not in arguments:
            return f"missing required property: {required}"
    if parameters.get("additionalProperties") is False:
        unknown = sorted(set(arguments) - set(properties))
        if unknown:
            return f"unknown property: {unknown[0]}"
    for key, value in arguments.items():
        property_schema = properties.get(key)
        if property_schema is None:
            continue
        expected = property_schema.get("type")
        valid = {
            "string": isinstance(value, str),
            "boolean": isinstance(value, bool),
            "integer": isinstance(value, int) and not isinstance(value, bool),
        }.get(expected, True)
        if not valid:
            article = "an" if expected == "integer" else "a"
            return f"{key} must be {article} {expected}"
        minimum = property_schema.get("minimum")
        if minimum is not None and value < minimum:
            return f"{key} must be at least {minimum}"
    return None
