from __future__ import annotations

from typing import Any


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "list_files",
        "description": "List repository files below an optional relative directory.",
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
        "description": "Apply a unified diff whose paths are relative to the repository root.",
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
