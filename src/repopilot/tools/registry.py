from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from repopilot.models import ToolResult
from repopilot.sandbox.docker import DockerSandbox
from repopilot.tools.contracts import TOOL_SCHEMAS


class ToolRegistry:
    def __init__(self, sandbox: DockerSandbox):
        self.sandbox = sandbox
        self.revision = 0
        self.unnecessary_calls = 0
        self._seen_calls: set[tuple[str, str, int]] = set()

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return TOOL_SCHEMAS

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        started = perf_counter()
        valid_names = {schema["name"] for schema in TOOL_SCHEMAS}
        if name not in valid_names:
            self.unnecessary_calls += 1
            return ToolResult(False, {}, (perf_counter() - started) * 1000, self.revision, f"unknown tool: {name}")
        if not isinstance(arguments, dict):
            self.unnecessary_calls += 1
            return ToolResult(False, {}, (perf_counter() - started) * 1000, self.revision, "tool arguments must be an object")

        call_key = (name, json.dumps(arguments, sort_keys=True), self.revision)
        if call_key in self._seen_calls:
            self.unnecessary_calls += 1
        self._seen_calls.add(call_key)

        response = self.sandbox.invoke(name, arguments)
        ok = bool(response.get("ok"))
        observation = response.get("result", {}) if ok else {}
        if not ok:
            self.unnecessary_calls += 1
        elif name == "apply_patch":
            if observation.get("changed"):
                self.revision += 1
            else:
                self.unnecessary_calls += 1
        return ToolResult(
            ok=ok,
            observation=observation,
            latency_ms=float(response.get("latency_ms", (perf_counter() - started) * 1000)),
            revision=self.revision,
            error=response.get("error"),
        )
