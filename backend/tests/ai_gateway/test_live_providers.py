"""Real calls to real providers. Skipped unless that provider's key is present.

These cost money and need the network, so they never run in the ordinary suite:

    uv run pytest -m live                      # every provider with a key set
    uv run pytest -m "live and openai"         # just one

They exist because the contract suite proves our mapping, not that the model IDs in
`llm.yaml` are real and reachable. This is what catches a stale model ID.
"""

from __future__ import annotations

import os

import pytest
from pydantic import BaseModel

from firebid.ai_gateway import build_adapter
from firebid.ai_gateway.config import load_config
from firebid.ai_gateway.types import GenerationRequest, Message, StopReason

pytestmark = pytest.mark.live


class Answer(BaseModel):
    city: str


CREDENTIALS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
}


def provider_params() -> list[pytest.param]:  # type: ignore[valid-type]
    return [
        pytest.param(
            name,
            marks=[
                getattr(pytest.mark, name),
                pytest.mark.skipif(not os.environ.get(variable), reason=f"{variable} is not set"),
            ],
            id=name,
        )
        for name, variable in CREDENTIALS.items()
    ]


@pytest.mark.parametrize("provider_name", provider_params())
def test_the_configured_model_answers(provider_name: str) -> None:
    """Proves the model ID in llm.yaml exists and the credentials work."""
    config = load_config()
    provider = config.providers[provider_name]
    model = next(
        model
        for model in config.models.values()
        if model.provider == provider_name and "embedding" not in model.capabilities
    )

    adapter = build_adapter(provider_name, provider)
    response = adapter.generate(
        GenerationRequest(
            messages=(Message.user("Reply with exactly: pong"),),
            max_output_tokens=64,
        ),
        model,
    )

    assert response.stop_reason in (StopReason.END, StopReason.MAX_OUTPUT)
    assert "pong" in response.text.lower()
    assert response.usage.output_tokens > 0


@pytest.mark.parametrize("provider_name", provider_params())
def test_structured_output_comes_back_valid(provider_name: str) -> None:
    config = load_config()
    provider = config.providers[provider_name]
    model = next(
        model
        for model in config.models.values()
        if model.provider == provider_name and "structured_output" in model.capabilities
    )

    adapter = build_adapter(provider_name, provider)
    response = adapter.generate(
        GenerationRequest(
            messages=(Message.user("Which city is the Merlion in? Answer as JSON."),),
            output_model=Answer,
            max_output_tokens=256,
        ),
        model,
    )
    assert isinstance(response.parsed, Answer)
    assert "singapore" in response.parsed.city.lower()
