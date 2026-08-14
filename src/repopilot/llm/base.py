from __future__ import annotations

from typing import Any, Protocol

from repopilot.models import ModelTurn


class ModelClient(Protocol):
    @property
    def metadata(self) -> dict[str, Any]: ...

    def next_action(
        self,
        *,
        issue: str,
        history: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
    ) -> ModelTurn: ...
