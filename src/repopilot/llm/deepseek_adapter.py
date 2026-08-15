from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from repopilot.llm.prompting import SYSTEM_PROMPT
from repopilot.models import AgentAction, ModelTurn, TokenUsage


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODELS = frozenset({"deepseek-v4-flash", "deepseek-v4-pro"})


def _chat_tool_schema(schema: dict[str, Any]) -> dict[str, Any]:
    function = {
        "name": schema["name"],
        "description": schema.get("description", ""),
        "parameters": schema.get("parameters", {"type": "object", "properties": {}}),
        "strict": bool(schema.get("strict", False)),
    }
    return {"type": "function", "function": function}


def _decode_tool_arguments(name: str, raw_arguments: str | None) -> dict[str, Any]:
    try:
        arguments = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"DeepSeek returned invalid JSON arguments for {name}") from exc
    if not isinstance(arguments, dict):
        raise ValueError(f"DeepSeek returned non-object arguments for {name}")
    return arguments


def _chat_transcript(issue: str, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Issue:\n{issue}"},
    ]
    for index, item in enumerate(history):
        if item["type"] in {"plan", "final"}:
            messages.append({"role": "assistant", "content": item["content"]})
            continue
        if item["type"] == "tool_result":
            call_id = f"call_repopilot_{index}"
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": item["tool"],
                                "arguments": json.dumps(item.get("arguments", {}), sort_keys=True),
                            },
                        }
                    ],
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(
                        {
                            "ok": item.get("ok"),
                            "observation": item.get("observation", {}),
                            "error": item.get("error"),
                            "workspace_revision": item.get("workspace_revision"),
                        },
                        sort_keys=True,
                    ),
                }
            )
            continue
        messages.append({"role": "user", "content": json.dumps(item, sort_keys=True)})
    return messages


class DeepSeekModel:
    """Official DeepSeek Chat Completions tool-calling adapter."""

    def __init__(self, model: str, *, api_key: str, client: Any | None = None):
        if model not in DEEPSEEK_MODELS:
            choices = " and ".join(sorted(DEEPSEEK_MODELS))
            raise ValueError(f"DeepSeek supports only {choices}")
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
        self._model = model
        self._client = client

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "provider": "deepseek",
            "model": self._model,
            "deterministic": False,
        }

    def next_action(
        self,
        *,
        issue: str,
        history: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
    ) -> ModelTurn:
        started = perf_counter()
        response = self._client.chat.completions.create(
            model=self._model,
            messages=_chat_transcript(issue, history),
            tools=[_chat_tool_schema(schema) for schema in tool_schemas],
            tool_choice="auto",
            # DeepSeek thinking tool turns require provider-specific reasoning
            # replay. RepoPilot deliberately uses the documented non-thinking
            # tool mode so its provider-neutral transcript remains unchanged.
            extra_body={"thinking": {"type": "disabled"}},
        )
        latency = (perf_counter() - started) * 1000
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise ValueError("DeepSeek returned no chat completion choices")
        message = choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []
        if tool_calls:
            function = tool_calls[0].function
            action = AgentAction(
                "tool",
                tool_name=function.name,
                arguments=_decode_tool_arguments(function.name, function.arguments),
            )
        else:
            text = str(getattr(message, "content", "") or "").strip()
            action = AgentAction("final" if text.upper().startswith("FINAL:") else "plan", content=text)

        usage = getattr(response, "usage", None)
        completion_details = getattr(usage, "completion_tokens_details", None)
        return ModelTurn(
            action=action,
            latency_ms=latency,
            usage=TokenUsage(
                input_tokens=getattr(usage, "prompt_tokens", None),
                output_tokens=getattr(usage, "completion_tokens", None),
                cached_tokens=getattr(usage, "prompt_cache_hit_tokens", None),
                reasoning_tokens=getattr(completion_details, "reasoning_tokens", None),
            ),
        )
