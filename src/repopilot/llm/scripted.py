from __future__ import annotations

from collections import deque
from time import perf_counter
from typing import Any, Iterable

from repopilot.models import AgentAction, ModelTurn, TokenUsage


class ScriptedModel:
    """Deterministic model used to regression-test the complete agent pipeline."""

    def __init__(self, actions: Iterable[AgentAction], name: str = "scripted-regression"):
        self._actions = deque(actions)
        self._name = name

    @property
    def metadata(self) -> dict[str, Any]:
        return {"provider": "scripted", "model": self._name, "deterministic": True}

    def next_action(
        self,
        *,
        issue: str,
        history: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
    ) -> ModelTurn:
        started = perf_counter()
        action = self._actions.popleft() if self._actions else AgentAction("final", content="Script exhausted.")
        return ModelTurn(
            action=action,
            latency_ms=(perf_counter() - started) * 1000,
            usage=TokenUsage(input_tokens=0, output_tokens=0),
        )
