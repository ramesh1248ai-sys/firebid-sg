"""Google Gemini, on the Gemini API or Vertex AI, through the Google Gen AI SDK.

The two access paths differ only in how the client is constructed, which is a configuration
choice (`platform: api` or `platform: vertex`), not a code path.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Literal

from firebid.ai_gateway.config import ModelConfig, ProviderConfig
from firebid.ai_gateway.errors import (
    AuthenticationFailed,
    BadRequest,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
)
from firebid.ai_gateway.providers.base import AdapterBase, parse_output
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
    "STOP": StopReason.END,
    "MAX_TOKENS": StopReason.MAX_OUTPUT,
    "SAFETY": StopReason.CONTENT_FILTER,
    "RECITATION": StopReason.CONTENT_FILTER,
    "PROHIBITED_CONTENT": StopReason.CONTENT_FILTER,
    "BLOCKLIST": StopReason.CONTENT_FILTER,
}


class GoogleAdapter(AdapterBase):
    def __init__(self, name: str, config: ProviderConfig, client: Any | None = None) -> None:
        super().__init__(name, config)
        self._client = client or self._build_client(config)

    @staticmethod
    def _build_client(config: ProviderConfig) -> Any:
        from google import genai

        if config.platform == "vertex":
            return genai.Client(
                vertexai=True, project=config.project_id, location=config.region or "global"
            )
        return genai.Client(api_key=config.resolve_credential())

    def _config(self, request: GenerationRequest, model: ModelConfig) -> dict[str, Any]:
        settings: dict[str, Any] = {
            "max_output_tokens": min(request.max_output_tokens, model.max_output_tokens or 1 << 30)
        }
        if request.system:
            settings["system_instruction"] = request.system
        if model.reasoning_parameter:
            settings[model.reasoning_parameter] = REASONING[str(request.reasoning)]
        if request.output_model is not None:
            settings["response_mime_type"] = "application/json"
            settings["response_schema"] = request.output_model
        if request.tools:
            settings["tools"] = [
                {
                    "function_declarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": dict(tool.input_schema),
                        }
                        for tool in request.tools
                    ]
                }
            ]
        settings.update(model.options)
        return settings

    def generate(self, request: GenerationRequest, model: ModelConfig) -> GenerationResponse:
        with _translated(self.name):
            response = self._client.models.generate_content(
                model=model.model_id,
                contents=[_content(message) for message in request.messages],
                config=self._config(request, model),
            )

        text = getattr(response, "text", None) or ""

        tool_calls = tuple(
            ToolCall(
                id=getattr(call, "id", None) or call.name,
                name=call.name,
                arguments=dict(call.args or {}),
            )
            for call in (getattr(response, "function_calls", None) or [])
        )

        finish = "STOP"
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            finish = str(getattr(candidates[0], "finish_reason", "STOP") or "STOP")
            finish = finish.rsplit(".", 1)[-1]  # FinishReason.STOP -> STOP
        stop_reason = STOP_REASONS.get(finish, StopReason.END)
        if tool_calls:
            stop_reason = StopReason.TOOL_CALL

        usage = getattr(response, "usage_metadata", None)
        return GenerationResponse(
            text=text,
            stop_reason=stop_reason,
            provider=self.name,
            model=model.model_id,
            usage=Usage(
                input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
                output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
                cached_input_tokens=getattr(usage, "cached_content_token_count", 0) or 0,
            ),
            parsed=parse_output(text, request.output_model) if text else None,
            tool_calls=tool_calls,
            raw_request_id=getattr(response, "response_id", None),
        )

    def stream(self, request: GenerationRequest, model: ModelConfig) -> Iterator[str]:
        with _translated(self.name):
            for chunk in self._client.models.generate_content_stream(
                model=model.model_id,
                contents=[_content(message) for message in request.messages],
                config=self._config(request, model),
            ):
                piece = getattr(chunk, "text", None)
                if piece:
                    yield piece

    def embed(self, texts: tuple[str, ...], model: ModelConfig) -> EmbeddingResponse:
        with _translated(self.name):
            response = self._client.models.embed_content(model=model.model_id, contents=list(texts))
        return EmbeddingResponse(
            vectors=tuple(tuple(item.values) for item in response.embeddings),
            provider=self.name,
            model=model.model_id,
        )


def _content(message: Message) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    for part in message.parts:
        if isinstance(part, TextPart):
            parts.append({"text": part.text})
        elif isinstance(part, ImagePart | DocumentPart):
            parts.append({"inline_data": {"mime_type": part.media_type, "data": part.data}})
    # Gemini calls the assistant "model".
    return {"role": "model" if message.role == "assistant" else "user", "parts": parts}


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
        from google.genai import errors as genai_errors

        if isinstance(exc, genai_errors.APIError):
            status = getattr(exc, "code", None)
            message = str(exc)
            if status == 429:
                raise RateLimited(message, provider=self.provider, status=status) from exc
            if status in (401, 403):
                raise AuthenticationFailed(message, provider=self.provider, status=status) from exc
            if status is not None and status >= 500:
                raise ProviderUnavailable(message, provider=self.provider, status=status) from exc
            if status is not None and 400 <= status < 500:
                raise BadRequest(message, provider=self.provider, status=status) from exc
            raise ProviderError(message, provider=self.provider, status=status) from exc
        return False
