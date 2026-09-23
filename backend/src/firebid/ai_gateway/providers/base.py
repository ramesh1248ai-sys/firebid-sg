"""What every provider adapter must offer, and the helpers they share.

An adapter's whole job is translation: our request in, the provider's SDK call, our response
out, and the provider's exceptions turned into ours. Policy — retries, fallbacks, data-class
enforcement — belongs to the router, never here.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError

from firebid.ai_gateway.config import ModelConfig, ProviderConfig
from firebid.ai_gateway.errors import OutputInvalid
from firebid.ai_gateway.types import (
    EmbeddingResponse,
    GenerationRequest,
    GenerationResponse,
)


@runtime_checkable
class Adapter(Protocol):
    """One provider. Constructed once per provider entry in `llm.yaml`."""

    name: str

    def generate(self, request: GenerationRequest, model: ModelConfig) -> GenerationResponse: ...

    def stream(self, request: GenerationRequest, model: ModelConfig) -> Iterator[str]: ...

    def embed(self, texts: tuple[str, ...], model: ModelConfig) -> EmbeddingResponse: ...


class AdapterBase:
    """Shared plumbing. Adapters subclass this for the bits that are genuinely common."""

    name: str

    def __init__(self, name: str, config: ProviderConfig) -> None:
        self.name = name
        self.config = config

    def stream(self, request: GenerationRequest, model: ModelConfig) -> Iterator[str]:
        """Adapters that cannot stream fall back to one call, yielded whole.

        Callers see the same interface; they simply get one chunk.
        """
        yield self.generate(request, model).text

    def embed(self, texts: tuple[str, ...], model: ModelConfig) -> EmbeddingResponse:
        from firebid.ai_gateway.errors import CapabilityMissing

        raise CapabilityMissing(f"provider '{self.name}' has no embedding endpoint")

    def generate(self, request: GenerationRequest, model: ModelConfig) -> GenerationResponse:
        raise NotImplementedError


def encode(data: bytes) -> str:
    """Base64 with no newlines, which every provider wants and some reject without."""
    return base64.standard_b64encode(data).decode("ascii")


def parse_output(text: str, output_model: type[BaseModel] | None) -> BaseModel | None:
    """Validate against the route's model whatever the provider did.

    Providers differ in how well they honour a schema, so the gateway always checks rather
    than trusting a provider's own validation.
    """
    if output_model is None:
        return None
    try:
        return output_model.model_validate_json(text)
    except ValidationError as error:
        raise OutputInvalid(f"output did not match {output_model.__name__}: {error}") from error
