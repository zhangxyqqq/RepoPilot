from __future__ import annotations

from time import perf_counter
from typing import Any

from repopilot.llm.prompting import build_transcript
from repopilot.models import AgentAction, ModelTurn, TokenUsage


class ResponsesModel:
    """Shared Responses API behavior, independent of endpoint ownership."""

    def __init__(self, model: str, *, provider: str, client: Any, base_url: str | None = None):
        self._model = model
        self._provider = provider
        self._client = client
        self._base_url = base_url

    @property
    def metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "provider": self._provider,
            "model": self._model,
            "deterministic": False,
        }
        if self._base_url is not None:
            metadata["base_url"] = self._base_url
        return metadata

    def next_action(
        self,
        *,
        issue: str,
        history: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
    ) -> ModelTurn:
        started = perf_counter()
        response = self._client.responses.create(
            model=self._model,
            input=build_transcript(issue, history),
            tools=tool_schemas,
            parallel_tool_calls=False,
        )
        latency = (perf_counter() - started) * 1000
        action: AgentAction | None = None
        for item in getattr(response, "output", []):
            if getattr(item, "type", None) == "function_call":
                action = AgentAction(
                    "tool",
                    tool_name=item.name,
                    arguments=_decode_arguments(item.name, item.arguments),
                )
                break
        if action is None:
            text = str(getattr(response, "output_text", "") or "").strip()
            action = AgentAction("final" if text.upper().startswith("FINAL:") else "plan", content=text)

        usage = getattr(response, "usage", None)
        input_details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)
        return ModelTurn(
            action=action,
            latency_ms=latency,
            usage=TokenUsage(
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
                cached_tokens=getattr(input_details, "cached_tokens", None),
                reasoning_tokens=getattr(output_details, "reasoning_tokens", None),
            ),
        )


class OpenAIModel(ResponsesModel):
    """Official OpenAI endpoint, preserving the original constructor API."""

    def __init__(self, model: str, *, client: Any | None = None):
        if client is None:
            from openai import OpenAI

            client = OpenAI()
        super().__init__(model, provider="openai", client=client)


class OpenAICompatibleModel(ResponsesModel):
    """Responses-compatible endpoint, suitable for a locally served model."""

    def __init__(
        self,
        model: str,
        *,
        base_url: str,
        api_key: str | None = None,
        client: Any | None = None,
    ):
        if client is None:
            from openai import OpenAI

            # Passing an explicit placeholder prevents the SDK from reading and
            # forwarding OPENAI_API_KEY to a separately configured endpoint.
            client = OpenAI(base_url=base_url, api_key=api_key or "not-required")
        super().__init__(
            model,
            provider="openai_compatible",
            client=client,
            base_url=base_url,
        )


def _decode_arguments(name: str, raw_arguments: str | None) -> dict[str, Any]:
    import json

    try:
        arguments = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"model returned invalid JSON arguments for {name}") from exc
    if not isinstance(arguments, dict):
        raise ValueError(f"model returned non-object arguments for {name}")
    return arguments
