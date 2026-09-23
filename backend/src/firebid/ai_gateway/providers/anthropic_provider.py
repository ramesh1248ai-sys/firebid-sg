"""Anthropic Claude, on the first-party API or through Bedrock, Vertex AI or Foundry.

Written against the Anthropic Python SDK. Two things here are easy to get wrong and are
deliberate:

* Reasoning depth is `output_config.effort`, and thinking is adaptive. `budget_tokens` is
  rejected with a 400 on current models, so it appears nowhere.
* `stop_details` is populated only when `stop_reason == "refusal"`, so it is read only then.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Literal

from firebid.ai_gateway.config import ModelConfig, ProviderConfig
from firebid.ai_gateway.errors import (
    AuthenticationFailed,
    BadRequest,
    ConfigError,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
)
from firebid.ai_gateway.providers.base import AdapterBase, encode, parse_output
from firebid.ai_gateway.types import (
    DocumentPart,
    GenerationRequest,
    GenerationResponse,
    ImagePart,
    Message,
    StopReason,
    TextPart,
    ToolCall,
    Usage,
)

# Our portable level -> the provider's own parameter.
EFFORT = {"low": "low", "medium": "high", "high": "max"}

STOP_REASONS = {
    "end_turn": StopReason.END,
    "stop_sequence": StopReason.END,
    "max_tokens": StopReason.MAX_OUTPUT,
    "tool_use": StopReason.TOOL_CALL,
    "refusal": StopReason.REFUSAL,
    "pause_turn": StopReason.END,
}


class AnthropicAdapter(AdapterBase):
    def __init__(self, name: str, config: ProviderConfig, client: Any | None = None) -> None:
        super().__init__(name, config)
        self._client = client or self._build_client(config)

    @staticmethod
    def _build_client(config: ProviderConfig) -> Any:
        import anthropic

        if config.platform == "bedrock":
            return anthropic.AnthropicBedrockMantle(aws_region=config.region)
        if config.platform == "vertex":
            if not config.project_id or not config.region:
                raise ConfigError(
                    "an Anthropic provider on Vertex AI needs project_id and region in llm.yaml"
                )
            return anthropic.AnthropicVertex(project_id=config.project_id, region=config.region)
        if config.platform == "foundry":
            return anthropic.AnthropicFoundry(
                api_key=config.resolve_credential(), resource=config.base_url
            )
        key = config.resolve_credential()
        # A bare client still resolves an `ant auth login` profile, so a missing key is not
        # necessarily an error.
        return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()

    def generate(self, request: GenerationRequest, model: ModelConfig) -> GenerationResponse:
        payload: dict[str, Any] = {
            "model": model.model_id,
            "max_tokens": min(request.max_output_tokens, model.max_output_tokens or 1 << 30),
            "messages": [_message(message) for message in request.messages],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": EFFORT[str(request.reasoning)]},
        }
        if request.system:
            payload["system"] = request.system
        if request.tools:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": dict(tool.input_schema),
                    "strict": True,
                }
                for tool in request.tools
            ]

        with _translated(self.name):
            if request.output_model is not None:
                # Native structured output; the gateway re-validates regardless.
                response = self._client.messages.parse(
                    output_format=request.output_model, **payload
                )
            else:
                response = self._client.messages.create(**payload)

        return self._to_response(response, request, model)

    def _to_response(
        self, response: Any, request: GenerationRequest, model: ModelConfig
    ) -> GenerationResponse:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                )
        text = "".join(text_parts)

        stop_reason = STOP_REASONS.get(response.stop_reason or "end_turn", StopReason.END)
        parsed = getattr(response, "parsed_output", None)
        if parsed is None and stop_reason is not StopReason.REFUSAL:
            parsed = parse_output(text, request.output_model)

        usage = getattr(response, "usage", None)
        return GenerationResponse(
            text=text,
            stop_reason=stop_reason,
            provider=self.name,
            model=model.model_id,
            usage=Usage(
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                cached_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            ),
            parsed=parsed,
            tool_calls=tuple(tool_calls),
            raw_request_id=getattr(response, "id", None),
        )

    def stream(self, request: GenerationRequest, model: ModelConfig) -> Iterator[str]:
        payload: dict[str, Any] = {
            "model": model.model_id,
            "max_tokens": min(request.max_output_tokens, model.max_output_tokens or 1 << 30),
            "messages": [_message(message) for message in request.messages],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": EFFORT[str(request.reasoning)]},
        }
        if request.system:
            payload["system"] = request.system
        with _translated(self.name), self._client.messages.stream(**payload) as stream:
            yield from stream.text_stream


def _message(message: Message) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for part in message.parts:
        if isinstance(part, TextPart):
            content.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": part.media_type,
                        "data": encode(part.data),
                    },
                }
            )
        elif isinstance(part, DocumentPart):
            content.append(
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": part.media_type,
                        "data": encode(part.data),
                    },
                }
            )
    return {"role": message.role, "content": content}


class _translated:
    """Turn the SDK's typed exceptions into ours, most specific first."""

    def __init__(self, provider: str) -> None:
        self.provider = provider

    def __enter__(self) -> None:
        return None

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object
    ) -> Literal[False]:
        if exc is None:
            return False
        import anthropic

        if isinstance(exc, anthropic.AuthenticationError):
            raise AuthenticationFailed(str(exc), provider=self.provider, status=401) from exc
        if isinstance(exc, anthropic.RateLimitError):
            raise RateLimited(str(exc), provider=self.provider, status=429) from exc
        if isinstance(exc, anthropic.BadRequestError | anthropic.NotFoundError):
            raise BadRequest(str(exc), provider=self.provider) from exc
        if isinstance(exc, anthropic.APIConnectionError | anthropic.APITimeoutError):
            raise ProviderUnavailable(str(exc), provider=self.provider) from exc
        if isinstance(exc, anthropic.APIStatusError):
            status = getattr(exc, "status_code", None)
            if status is not None and status >= 500:
                raise ProviderUnavailable(str(exc), provider=self.provider, status=status) from exc
            raise ProviderError(str(exc), provider=self.provider, status=status) from exc
        return False
