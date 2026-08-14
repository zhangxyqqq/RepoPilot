from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None

    def add(self, other: "TokenUsage") -> "TokenUsage":
        def total(left: int | None, right: int | None) -> int | None:
            if left is None and right is None:
                return None
            return (left or 0) + (right or 0)

        return TokenUsage(
            input_tokens=total(self.input_tokens, other.input_tokens),
            output_tokens=total(self.output_tokens, other.output_tokens),
            cached_tokens=total(self.cached_tokens, other.cached_tokens),
            reasoning_tokens=total(self.reasoning_tokens, other.reasoning_tokens),
        )


@dataclass(frozen=True)
class AgentAction:
    kind: Literal["tool", "plan", "final"]
    tool_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    content: str = ""


@dataclass(frozen=True)
class ModelTurn:
    action: AgentAction
    latency_ms: float
    usage: TokenUsage = TokenUsage()


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    observation: dict[str, Any]
    latency_ms: float
    revision: int
    error: str | None = None


@dataclass
class RunResult:
    run_id: str
    success: bool
    stop_reason: str
    iterations: int
    repair_cycles: int
    tool_calls: int
    unnecessary_tool_calls: int
    latency_ms: float
    usage: TokenUsage
    final_message: str
    final_diff: str
    changed_files: list[str]
    final_test: dict[str, Any] | None
    trajectory_path: str
