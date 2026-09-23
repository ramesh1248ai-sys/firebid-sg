"""The AI gateway: every model call in the platform goes through here.

Calling code names a route and never a provider:

    from firebid.ai_gateway import gateway
    from firebid.ai_gateway.types import GenerationRequest, Message

    answer = gateway().generate(
        "title_block_read",
        GenerationRequest(messages=(Message.user("..."),), output_model=TitleBlock),
    )

Which provider and model served it is decided by `backend/config/llm.yaml`.
"""

from __future__ import annotations

from functools import lru_cache

from firebid.ai_gateway.config import LlmConfig, ProviderConfig, get_config
from firebid.ai_gateway.errors import (
    ConfigError,
    DataClassRefused,
    GatewayError,
    NoModelAvailable,
    OutputInvalid,
)
from firebid.ai_gateway.providers.base import Adapter
from firebid.ai_gateway.router import Router
from firebid.ai_gateway.types import (
    Capability,
    DataClass,
    GenerationRequest,
    GenerationResponse,
    Message,
    ReasoningLevel,
    StopReason,
)

__all__ = [
    "Adapter",
    "Capability",
    "ConfigError",
    "DataClass",
    "DataClassRefused",
    "GatewayError",
    "GenerationRequest",
    "GenerationResponse",
    "LlmConfig",
    "Message",
    "NoModelAvailable",
    "OutputInvalid",
    "ReasoningLevel",
    "Router",
    "StopReason",
    "build_adapters",
    "gateway",
]


def build_adapter(name: str, config: ProviderConfig) -> Adapter:
    """One adapter per provider entry. The `kind` field picks the implementation."""
    if config.kind == "anthropic":
        from firebid.ai_gateway.providers.anthropic_provider import AnthropicAdapter

        return AnthropicAdapter(name, config)
    if config.kind in ("openai", "openai_compatible"):
        from firebid.ai_gateway.providers.openai_provider import OpenAIAdapter

        return OpenAIAdapter(name, config)
    if config.kind == "google":
        from firebid.ai_gateway.providers.google_provider import GoogleAdapter

        return GoogleAdapter(name, config)
    if config.kind == "fake":
        from firebid.ai_gateway.providers.fake import FakeAdapter

        return FakeAdapter(name, config)
    raise ConfigError(f"provider '{name}' has unknown kind '{config.kind}'")


def build_adapters(config: LlmConfig) -> dict[str, Adapter]:
    """Adapters for every enabled provider. A disabled provider is never constructed, so a
    missing credential for something switched off is not an error."""
    return {
        name: build_adapter(name, provider)
        for name, provider in config.providers.items()
        if provider.enabled
    }


@lru_cache
def gateway() -> Router:
    config = get_config()
    return Router(config=config, adapters=build_adapters(config))
