"""OpenAI, on the OpenAI API or Azure OpenAI, and any OpenAI-compatible endpoint.

Structured output is requested with a JSON schema rather than through an SDK `parse()` helper,
so the adapter does not depend on where that helper lives in a given SDK version. The gateway
validates the result against the route's model either way.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, Literal

from firebid.ai_gateway.config import ModelConfig, ProviderConfig
from firebid.ai_gateway.errors import (
    AuthenticationFailed,
    BadRequest,
    CapabilityMissing,
    ConfigError,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
)
from firebid.ai_gateway.providers.base import AdapterBase, encode, parse_output
from firebid.ai_gateway.types import (
    DocumentPart,
    EmbeddingResponse,
    GenerationRequest,
    GenerationResponse,
    ImagePart,
    Message,
    StopReason,
    TextPart,
    ToolCall,
    Usage,
)

REASONING = {"low": "low", "medium": "medium", "high": "high"}

STOP_REASONS = {
    "stop": StopReason.END,
    "length": StopReason.MAX_OUTPUT,
    "tool_calls": StopReason.TOOL_CALL,
    "function_call": StopReason.TOOL_CALL,
    "content_filter": StopReason.CONTENT_FILTER,
}


class OpenAIAdapter(AdapterBase):
    def __init__(self, name: str, config: ProviderConfig, client: Any | None = None) -> None:
        super().__init__(name, config)
        self._client = client or self._build_client(config)

    @staticmethod
    def _build_client(config: ProviderConfig) -> Any:
        import openai

        key = config.resolve_credential()
        if config.platform == "azure":
            api_version = config.options.get("api_version")
            if not api_version:
                raise ConfigError(
                    f"provider '{config.kind}' on Azure needs options.api_version in llm.yaml"
                )
            return openai.AzureOpenAI(
                api_key=key,
                azure_endpoint=config.base_url or "",
                api_version=str(api_version),
            )
        # An OpenAI-compatible endpoint (vLLM and friends) differs only by base_url, and
        # usually wants no real key.
        return openai.OpenAI(api_key=key or "not-needed", base_url=config.base_url)

    def generate(self, request: GenerationRequest, model: ModelConfig) -> GenerationResponse:
        payload: dict[str, Any] = {
            "model": model.model_id,
            "messages": _messages(request),
            "max_completion_tokens": min(
                request.max_output_tokens, model.max_output_tokens or 1 << 30
            ),
        }
        if model.reasoning_parameter:
            payload[model.reasoning_parameter] = REASONING[str(request.reasoning)]
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": dict(tool.input_schema),
                    },
                }
                for tool in request.tools
            ]
        if request.output_model is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.output_model.__name__,
                    "schema": request.output_model.model_json_schema(),
                    "strict": True,
                },
            }
        payload.update(model.options)

        with _translated(self.name):
            response = self._client.chat.completions.create(**payload)

        choice = response.choices[0]
        message = choice.message
        text = message.content or ""

        tool_calls: list[ToolCall] = []
        for call in getattr(message, "tool_calls", None) or []:
            # Arguments arrive as a JSON string; never string-match them.
            raw = call.function.arguments or "{}"
            try:
                arguments = json.loads(raw)
            except json.JSONDecodeError:
                arguments = {"_unparsed": raw}
            tool_calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))

        stop_reason = STOP_REASONS.get(choice.finish_reason or "stop", StopReason.END)
        usage = getattr(response, "usage", None)
        cached = 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", 0) or 0

        return GenerationResponse(
            text=text,
            stop_reason=stop_reason,
            provider=self.name,
            model=model.model_id,
            usage=Usage(
                input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                cached_input_tokens=cached,
            ),
            parsed=parse_output(text, request.output_model) if text else None,
            tool_calls=tuple(tool_calls),
            raw_request_id=getattr(response, "id", None),
        )

    def stream(self, request: GenerationRequest, model: ModelConfig) -> Iterator[str]:
        payload: dict[str, Any] = {
            "model": model.model_id,
            "messages": _messages(request),
            "max_completion_tokens": min(
                request.max_output_tokens, model.max_output_tokens or 1 << 30
            ),
            "stream": True,
        }
        if model.reasoning_parameter:
            payload[model.reasoning_parameter] = REASONING[str(request.reasoning)]
        payload.update(model.options)
        with _translated(self.name):
            for chunk in self._client.chat.completions.create(**payload):
                if not chunk.choices:
                    continue
                piece = chunk.choices[0].delta.content
                if piece:
                    yield piece

    def embed(self, texts: tuple[str, ...], model: ModelConfig) -> EmbeddingResponse:
        with _translated(self.name):
            response = self._client.embeddings.create(model=model.model_id, input=list(texts))
        usage = getattr(response, "usage", None)
        return EmbeddingResponse(
            vectors=tuple(tuple(item.embedding) for item in response.data),
            provider=self.name,
            model=model.model_id,
            usage=Usage(input_tokens=getattr(usage, "prompt_tokens", 0) or 0),
        )


def _messages(request: GenerationRequest) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if request.system:
        messages.append({"role": "system", "content": request.system})
    messages.extend(_message(message) for message in request.messages)
    return messages


def _message(message: Message) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for part in message.parts:
        if isinstance(part, TextPart):
            content.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            data_url = f"data:{part.media_type};base64,{encode(part.data)}"
            content.append({"type": "image_url", "image_url": {"url": data_url}})
        elif isinstance(part, DocumentPart):
            # Not claimed as a capability in llm.yaml, so a route needing it never selects
            # this model. Refusing loudly beats silently dropping the document.
            raise CapabilityMissing(
                "this OpenAI adapter does not send documents; render pages to images, "
                "or route to a model whose capabilities include pdf_input"
            )
    return {"role": message.role, "content": content}


class _translated:
    def __init__(self, provider: str) -> None:
        self.provider = provider

    def __enter__(self) -> None:
        return None

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object
    ) -> Literal[False]:
        if exc is None:
            return False
        import openai

        if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
            raise AuthenticationFailed(str(exc), provider=self.provider, status=401) from exc
        if isinstance(exc, openai.RateLimitError):
            raise RateLimited(str(exc), provider=self.provider, status=429) from exc
        if isinstance(exc, openai.BadRequestError | openai.NotFoundError):
            raise BadRequest(str(exc), provider=self.provider) from exc
        if isinstance(
            exc, openai.APIConnectionError | openai.APITimeoutError | openai.InternalServerError
        ):
            raise ProviderUnavailable(str(exc), provider=self.provider) from exc
        if isinstance(exc, openai.APIStatusError):
            status = getattr(exc, "status_code", None)
            if status is not None and status >= 500:
                raise ProviderUnavailable(str(exc), provider=self.provider, status=status) from exc
            raise ProviderError(str(exc), provider=self.provider, status=status) from exc
        return False
