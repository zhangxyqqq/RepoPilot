from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from repopilot.models import ToolResult
from repopilot.tools.definitions import ToolDefinition


@runtime_checkable
class ToolBackend(Protocol):
    @property
    def definitions(self) -> tuple[ToolDefinition, ...]: ...

    @property
    def schemas(self) -> list[dict[str, Any]]: ...

    @property
    def revision(self) -> int: ...

    @property
    def unnecessary_calls(self) -> int: ...

    @unnecessary_calls.setter
    def unnecessary_calls(self, value: int) -> None: ...

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult: ...
