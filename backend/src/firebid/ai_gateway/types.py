"""The provider-neutral vocabulary every adapter speaks.

Calling code names a route and passes these types. It never names a provider or a model, and
it never sees a provider's own request or response shape (project-context, "LLM providers").
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class DataClass(StrEnum):
    """How sensitive the data in a call is. A route declares the highest class it sends."""

    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"  # tender documents, drawings, quantities
    COMMERCIAL = "commercial"  # prices, quotations, margins
    PERSONAL = "personal"  # PDPA


class Capability(StrEnum):
    """What a model can do. A route declares what it needs; startup checks the chain has it."""

    VISION = "vision"
    PDF_INPUT = "pdf_input"
    STRUCTURED_OUTPUT = "structured_output"
    TOOLS = "tools"
    STREAMING = "streaming"
    CITATIONS = "citations"
    CACHING = "caching"
    BATCH = "batch"
    EMBEDDING = "embedding"


class ReasoningLevel(StrEnum):
    """Portable reasoning depth. Each adapter maps it to its provider's own parameter."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class StopReason(StrEnum):
    """Why generation stopped, normalised across providers."""

    END = "end"
    MAX_OUTPUT = "max_output"
    REFUSAL = "refusal"
    TOOL_CALL = "tool_call"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class ImagePart:
    """Raw bytes, not base64: each adapter encodes the way its provider wants."""

    media_type: str
    data: bytes


@dataclass(frozen=True)
class DocumentPart:
    media_type: str
    data: bytes
    name: str = "document"


ContentPart = TextPart | ImagePart | DocumentPart


@dataclass(frozen=True)
class Message:
    role: str  # "user" or "assistant"
    parts: tuple[ContentPart, ...]

    @staticmethod
    def user(text: str) -> Message:
        return Message(role="user", parts=(TextPart(text),))

    @staticmethod
    def assistant(text: str) -> Message:
        return Message(role="assistant", parts=(TextPart(text),))

    def text(self) -> str:
        return "".join(part.text for part in self.parts if isinstance(part, TextPart))


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True)
class GenerationRequest:
    """One model call, before a route has chosen who serves it."""

    messages: tuple[Message, ...]
    system: str | None = None
    tools: tuple[ToolDef, ...] = ()
    # When set, the adapter asks for structured output and the gateway validates the result
    # against this model whatever the provider did.
    output_model: type[BaseModel] | None = None
    reasoning: ReasoningLevel = ReasoningLevel.MEDIUM
    max_output_tokens: int = 16_000

    def requires(self) -> frozenset[Capability]:
        """What this particular request needs, regardless of what the route declared."""
        needed: set[Capability] = set()
        if self.output_model is not None:
            needed.add(Capability.STRUCTURED_OUTPUT)
        if self.tools:
            needed.add(Capability.TOOLS)
        for message in self.messages:
            for part in message.parts:
                if isinstance(part, ImagePart):
                    needed.add(Capability.VISION)
                elif isinstance(part, DocumentPart):
                    needed.add(Capability.PDF_INPUT)
        return frozenset(needed)


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0


@dataclass(frozen=True)
class Attempt:
    """One try against one model. The router records every attempt, successful or not."""

    provider: str
    model: str
    ok: bool
    reason: str | None = None


@dataclass(frozen=True)
class GenerationResponse:
    """The normalised response. `raw_request_id` is kept so support can trace a call."""

    text: str
    stop_reason: StopReason
    provider: str
    model: str
    usage: Usage = field(default_factory=Usage)
    parsed: BaseModel | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    raw_request_id: str | None = None
    route: str | None = None
    prompt_version: str | None = None
    attempts: tuple[Attempt, ...] = ()


@dataclass(frozen=True)
class EmbeddingResponse:
    vectors: tuple[tuple[float, ...], ...]
    provider: str
    model: str
    usage: Usage = field(default_factory=Usage)


def as_sequence(messages: Sequence[Message] | Message) -> tuple[Message, ...]:
    return (messages,) if isinstance(messages, Message) else tuple(messages)
