from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

from repopilot.llm.base import ModelClient
from repopilot.llm.deepseek_adapter import DEEPSEEK_MODELS, DeepSeekModel
from repopilot.llm.openai_adapter import OpenAICompatibleModel, OpenAIModel


ProviderName = Literal["openai", "openai_compatible", "deepseek"]


@dataclass(frozen=True)
class ProviderConfig:
    provider: ProviderName
    model: str
    base_url: str | None = None
    api_key_env: str | None = None

    def validate(self) -> None:
        if not self.model.strip():
            raise ValueError("model must be non-empty")
        if self.provider == "openai":
            if self.base_url is not None or self.api_key_env is not None:
                raise ValueError("base_url and api_key_env are only valid for openai_compatible")
            return
        if self.provider == "deepseek":
            if self.base_url is not None or self.api_key_env is not None:
                raise ValueError("deepseek cannot override base_url or api_key_env")
            if self.model not in DEEPSEEK_MODELS:
                raise ValueError("deepseek supports only deepseek-v4-flash and deepseek-v4-pro")
            return
        if self.provider != "openai_compatible":
            raise ValueError(f"unsupported model provider: {self.provider}")
        if not self.base_url:
            raise ValueError("openai_compatible requires base_url")
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("base_url must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain a query string or fragment")
        if self.api_key_env is not None and not self.api_key_env.strip():
            raise ValueError("api_key_env must be non-empty when provided")


def create_model(
    config: ProviderConfig,
    *,
    environ: Mapping[str, str] | None = None,
    client: Any | None = None,
) -> ModelClient:
    config.validate()
    if config.provider == "openai":
        return OpenAIModel(config.model, client=client)

    environment = os.environ if environ is None else environ
    if config.provider == "deepseek":
        api_key = environment.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is required for the deepseek provider")
        return DeepSeekModel(config.model, api_key=api_key, client=client)

    api_key: str | None = None
    if config.api_key_env is not None:
        api_key = environment.get(config.api_key_env)
        if not api_key:
            raise ValueError(f"configured API key environment variable is not set: {config.api_key_env}")
    assert config.base_url is not None
    return OpenAICompatibleModel(
        config.model,
        base_url=config.base_url,
        api_key=api_key,
        client=client,
    )
