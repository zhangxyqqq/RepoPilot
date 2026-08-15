from types import SimpleNamespace

import pytest

from repopilot.llm import ProviderConfig, create_model
from repopilot.llm.deepseek_adapter import DeepSeekModel
from repopilot.llm.openai_adapter import OpenAICompatibleModel, OpenAIModel
from repopilot.models import TokenUsage


class FakeResponses:
    def __init__(self, response):
        self.response = response
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeClient:
    def __init__(self, response):
        self.responses = FakeResponses(response)


class FakeChatCompletions:
    def __init__(self, response):
        self.response = response
        self.calls: list[dict[str, object]] = []

    def create(
        self,
        *,
        model,
        messages,
        tools,
        tool_choice,
        extra_body,
    ):
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "tools": tools,
                "tool_choice": tool_choice,
                "extra_body": extra_body,
            }
        )
        return self.response


class FakeChatClient:
    def __init__(self, response):
        self.chat = SimpleNamespace(completions=FakeChatCompletions(response))


def test_openai_model_parses_tool_call_and_usage_without_network():
    response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="function_call",
                name="read_file",
                arguments='{"path": "app.py", "start_line": 1}',
            )
        ],
        output_text="",
        usage=SimpleNamespace(
            input_tokens=120,
            output_tokens=30,
            input_tokens_details=SimpleNamespace(cached_tokens=40),
            output_tokens_details=SimpleNamespace(reasoning_tokens=5),
        ),
    )
    client = FakeClient(response)
    model = OpenAIModel("test-model", client=client)

    turn = model.next_action(
        issue="Fix the bug",
        history=[],
        tool_schemas=[{"type": "function", "name": "read_file"}],
    )

    assert model.metadata == {
        "provider": "openai",
        "model": "test-model",
        "deterministic": False,
    }
    assert turn.action.kind == "tool"
    assert turn.action.tool_name == "read_file"
    assert turn.action.arguments == {"path": "app.py", "start_line": 1}
    assert turn.usage == TokenUsage(
        input_tokens=120,
        output_tokens=30,
        cached_tokens=40,
        reasoning_tokens=5,
    )
    request = client.responses.calls[0]
    assert request["model"] == "test-model"
    assert request["parallel_tool_calls"] is False


def test_compatible_model_preserves_missing_usage_as_none():
    client = FakeClient(SimpleNamespace(output=[], output_text="FINAL: complete"))
    model = OpenAICompatibleModel(
        "local-code-model",
        base_url="http://127.0.0.1:8000/v1",
        client=client,
    )

    turn = model.next_action(issue="Fix it", history=[], tool_schemas=[])

    assert model.metadata == {
        "provider": "openai_compatible",
        "model": "local-code-model",
        "base_url": "http://127.0.0.1:8000/v1",
        "deterministic": False,
    }
    assert turn.action.kind == "final"
    assert turn.usage == TokenUsage()


def test_deepseek_model_translates_chat_tool_call_and_usage_without_network():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name="read_file",
                                arguments='{"path": "app.py", "start_line": 2}',
                            )
                        )
                    ],
                )
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=150,
            completion_tokens=42,
            prompt_cache_hit_tokens=90,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=12),
        ),
    )
    client = FakeChatClient(response)
    model = DeepSeekModel("deepseek-v4-pro", api_key="secret", client=client)
    tool_schema = {
        "type": "function",
        "name": "read_file",
        "description": "Read a file.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "strict": False,
    }

    turn = model.next_action(issue="Fix it", history=[], tool_schemas=[tool_schema])

    assert turn.action.tool_name == "read_file"
    assert turn.action.arguments == {"path": "app.py", "start_line": 2}
    assert turn.usage == TokenUsage(
        input_tokens=150,
        output_tokens=42,
        cached_tokens=90,
        reasoning_tokens=12,
    )
    assert model.metadata == {
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "deterministic": False,
    }
    request = client.chat.completions.calls[0]
    assert request["model"] == "deepseek-v4-pro"
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert request["tool_choice"] == "auto"
    assert request["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file.",
                "parameters": tool_schema["parameters"],
                "strict": False,
            },
        }
    ]


def test_deepseek_model_rejects_malformed_tool_arguments():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(name="read_file", arguments="not-json")
                        )
                    ],
                )
            )
        ],
        usage=None,
    )
    model = DeepSeekModel(
        "deepseek-v4-flash",
        api_key="secret",
        client=FakeChatClient(response),
    )

    with pytest.raises(ValueError, match="invalid JSON arguments"):
        model.next_action(issue="Fix it", history=[], tool_schemas=[])


def test_deepseek_model_translates_neutral_history_to_native_tool_messages():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="PLAN: inspect the result", tool_calls=[])
            )
        ],
        usage=None,
    )
    client = FakeChatClient(response)
    model = DeepSeekModel("deepseek-v4-pro", api_key="secret", client=client)
    history = [
        {
            "type": "tool_result",
            "tool": "read_file",
            "arguments": {"path": "app.py"},
            "ok": True,
            "observation": {"content": "value = 1"},
            "error": None,
            "workspace_revision": 0,
        }
    ]

    model.next_action(issue="Fix it", history=history, tool_schemas=[])

    messages = client.chat.completions.calls[0]["messages"]
    assistant = messages[2]
    observation = messages[3]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["function"] == {
        "name": "read_file",
        "arguments": '{"path": "app.py"}',
    }
    assert observation["role"] == "tool"
    assert observation["tool_call_id"] == assistant["tool_calls"][0]["id"]
    assert '"content": "value = 1"' in observation["content"]


def test_factory_uses_explicit_placeholder_instead_of_openai_key(monkeypatch):
    captured: dict[str, object] = {}
    fake_client = FakeClient(SimpleNamespace(output=[], output_text="FINAL: complete"))

    def fake_openai(**kwargs):
        captured.update(kwargs)
        return fake_client

    monkeypatch.setattr("openai.OpenAI", fake_openai)
    model = create_model(
        ProviderConfig(
            provider="openai_compatible",
            model="local-code-model",
            base_url="http://localhost:8000/v1",
        ),
        environ={"OPENAI_API_KEY": "must-not-be-forwarded"},
    )

    assert captured == {
        "base_url": "http://localhost:8000/v1",
        "api_key": "not-required",
    }
    assert "api_key" not in model.metadata


def test_factory_reads_only_configured_compatible_api_key(monkeypatch):
    captured: dict[str, object] = {}

    def fake_openai(**kwargs):
        captured.update(kwargs)
        return FakeClient(SimpleNamespace(output=[], output_text=""))

    monkeypatch.setattr("openai.OpenAI", fake_openai)
    create_model(
        ProviderConfig(
            provider="openai_compatible",
            model="local-code-model",
            base_url="https://inference.example/v1",
            api_key_env="LOCAL_MODEL_API_KEY",
        ),
        environ={"LOCAL_MODEL_API_KEY": "endpoint-secret", "OPENAI_API_KEY": "openai-secret"},
    )

    assert captured["api_key"] == "endpoint-secret"


def test_factory_uses_only_deepseek_key_and_does_not_expose_it(monkeypatch):
    captured: dict[str, object] = {}

    def fake_openai(**kwargs):
        captured.update(kwargs)
        return FakeChatClient(
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="FINAL: done", tool_calls=[]))],
                usage=None,
            )
        )

    monkeypatch.setattr("openai.OpenAI", fake_openai)
    model = create_model(
        ProviderConfig(provider="deepseek", model="deepseek-v4-flash"),
        environ={
            "DEEPSEEK_API_KEY": "deepseek-secret",
            "OPENAI_API_KEY": "must-not-be-used",
        },
    )

    assert captured == {
        "api_key": "deepseek-secret",
        "base_url": "https://api.deepseek.com",
    }
    assert "deepseek-secret" not in repr(model.metadata)


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (ProviderConfig(provider="openai_compatible", model="model"), "requires base_url"),
        (
            ProviderConfig(provider="openai", model="model", base_url="http://localhost:8000/v1"),
            "only valid for openai_compatible",
        ),
        (
            ProviderConfig(
                provider="openai_compatible",
                model="model",
                base_url="http://token@localhost:8000/v1",
            ),
            "must not contain credentials",
        ),
        (
            ProviderConfig(provider="deepseek", model="unsupported-model"),
            "supports only deepseek-v4-flash and deepseek-v4-pro",
        ),
        (
            ProviderConfig(
                provider="deepseek",
                model="deepseek-v4-pro",
                base_url="https://example.invalid",
            ),
            "cannot override base_url or api_key_env",
        ),
    ],
)
def test_provider_config_rejects_unsafe_or_mismatched_options(config, message):
    with pytest.raises(ValueError, match=message):
        config.validate()


def test_factory_requires_explicitly_configured_key_variable():
    config = ProviderConfig(
        provider="openai_compatible",
        model="local-code-model",
        base_url="http://localhost:8000/v1",
        api_key_env="LOCAL_MODEL_API_KEY",
    )
    with pytest.raises(ValueError, match="LOCAL_MODEL_API_KEY"):
        create_model(config, environ={})


def test_factory_requires_deepseek_key_without_falling_back_to_openai_key():
    config = ProviderConfig(provider="deepseek", model="deepseek-v4-pro")

    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        create_model(config, environ={"OPENAI_API_KEY": "unrelated"})
