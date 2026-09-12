from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from repopilot.models import FailureMetadata, ToolResult
from repopilot.sandbox.docker import DockerSandbox
from repopilot.tools.contracts import validate_tool_arguments
from repopilot.tools.definitions import PUBLIC_TOOL_BY_NAME, PUBLIC_TOOL_DEFINITIONS, ToolDefinition, provider_tool_schemas


class ToolRegistry:
    def __init__(self, sandbox: DockerSandbox):
        self.sandbox = sandbox
        self._revision = 0
        self._unnecessary_calls = 0
        self._seen_calls: set[tuple[str, str, int]] = set()

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return PUBLIC_TOOL_DEFINITIONS

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return provider_tool_schemas()

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def unnecessary_calls(self) -> int:
        return self._unnecessary_calls

    @unnecessary_calls.setter
    def unnecessary_calls(self, value: int) -> None:
        self._unnecessary_calls = value

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        started = perf_counter()
        if name not in PUBLIC_TOOL_BY_NAME:
            self.unnecessary_calls += 1
            message = f"unknown tool: {name}"
            return ToolResult(
                False, {}, (perf_counter() - started) * 1000, self.revision, message,
                FailureMetadata("unknown_tool", message, False, "pre_execution", "safe_read"),
            )
        if not isinstance(arguments, dict):
            self.unnecessary_calls += 1
            message = "tool arguments must be an object"
            return ToolResult(
                False, {}, (perf_counter() - started) * 1000, self.revision, message,
                FailureMetadata("invalid_arguments", message, False, "pre_execution", "safe_read"),
            )
        #工具名合法还不够，参数也得符合这个工具的 schema。比如 read_file 需要 path，如果 LLM 乱传成数字、漏字段、或者塞了不该有的参数，这里就会拦掉。
        validation_error = validate_tool_arguments(name, arguments)
        if validation_error is not None:
            self.unnecessary_calls += 1
            message = f"invalid tool arguments: {validation_error}"
            return ToolResult(
                False,
                {},
                (perf_counter() - started) * 1000,
                self.revision,
                message,
                FailureMetadata("invalid_arguments", message, False, "pre_execution", "safe_read"),
            )
        #意思是：同一个工具 + 同一组参数 + 同一个代码版本，如果之前已经调用过，再调一次，就很可能是在浪费动作，所以 unnecessary_calls += 1。
        call_key = (name, json.dumps(arguments, sort_keys=True), self.revision)
        if call_key in self._seen_calls:
            self.unnecessary_calls += 1
        self._seen_calls.add(call_key)
        #好了，校验都通过了，现在真的去 Docker sandbox 里面执行 list_files / read_file / apply_patch / run_tests。
        response = self.sandbox.invoke(name, arguments)
        ok = bool(response.get("ok"))
        observation = response.get("result", {}) if ok else {}
        if not ok:
            self.unnecessary_calls += 1
        elif name == "apply_patch":
            if observation.get("changed"):
                self._revision += 1
            else:
                self.unnecessary_calls += 1
        error = response.get("error")
        failure = self._failure(name, str(error)) if not ok and error else None
        return ToolResult(
            ok=ok,
            observation=observation,
            latency_ms=float(response.get("latency_ms", (perf_counter() - started) * 1000)),
            revision=self.revision,
            error=error,
            failure=failure,
        )

    @staticmethod
    def _failure(name: str, message: str) -> FailureMetadata:
        lowered = message.lower()
        definition = PUBLIC_TOOL_BY_NAME[name]
        if any(term in lowered for term in ("protected", "must remain inside", "not allowlisted", "denied")):
            return FailureMetadata("policy_rejected", message, False, "pre_execution", "policy")
        if name == "apply_patch" and any(term in lowered for term in ("patch", "hunk", "context")):
            return FailureMetadata("patch_rejected", message, False, "pre_execution", "non_idempotent_mutation")
        if "timeout" in lowered or "timed out" in lowered:
            return FailureMetadata("timeout", message, True, "ambiguous_execution", definition.idempotency)
        execution_state = "ambiguous_execution" if definition.access == "mutating" else "post_execution"
        return FailureMetadata("tool_error", message, False, execution_state, definition.idempotency)
