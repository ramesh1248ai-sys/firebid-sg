"""A deterministic adapter, so tests and CI never call a real provider.

It is scriptable: queue replies or failures and it serves them in order. Without a script it
echoes, which keeps simple tests short.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator

from firebid.ai_gateway.config import ModelConfig, ProviderConfig
from firebid.ai_gateway.providers.base import AdapterBase, parse_output
from firebid.ai_gateway.types import (
    EmbeddingResponse,
    GenerationRequest,
    GenerationResponse,
    StopReason,
    ToolCall,
    Usage,
)


class FakeAdapter(AdapterBase):
    """Scripted replies, in order. Anything queued that is an exception is raised instead."""

    def __init__(self, name: str = "fake", config: ProviderConfig | None = None) -> None:
        super().__init__(name, config or ProviderConfig(kind="fake"))
        self._script: deque[GenerationResponse | Exception] = deque()
        self.calls: list[tuple[str, GenerationRequest]] = []

    def queue(self, *items: GenerationResponse | Exception) -> FakeAdapter:
        self._script.extend(items)
        return self

    def reply(
        self,
        text: str,
        *,
        stop_reason: StopReason = StopReason.END,
        tool_calls: tuple[ToolCall, ...] = (),
        model: str = "fake-1",
    ) -> FakeAdapter:
        return self.queue(
            GenerationResponse(
                text=text,
                stop_reason=stop_reason,
                provider=self.name,
                model=model,
                usage=Usage(input_tokens=10, output_tokens=5),
                tool_calls=tool_calls,
                raw_request_id="fake-request",
            )
        )

    def generate(self, request: GenerationRequest, model: ModelConfig) -> GenerationResponse:
        self.calls.append((model.model_id, request))
        if self._script:
            item = self._script.popleft()
            if isinstance(item, Exception):
                raise item
            parsed = parse_output(item.text, request.output_model)
            return GenerationResponse(
                text=item.text,
                stop_reason=item.stop_reason,
                provider=self.name,
                model=model.model_id,
                usage=item.usage,
                parsed=parsed,
                tool_calls=item.tool_calls,
                raw_request_id=item.raw_request_id,
            )

        echoed = request.messages[-1].text() if request.messages else ""
        return GenerationResponse(
            text=echoed,
            stop_reason=StopReason.END,
            provider=self.name,
            model=model.model_id,
            usage=Usage(input_tokens=len(echoed.split()), output_tokens=len(echoed.split())),
            parsed=parse_output(echoed, request.output_model),
            raw_request_id="fake-request",
        )

    def stream(self, request: GenerationRequest, model: ModelConfig) -> Iterator[str]:
        for word in self.generate(request, model).text.split(" "):
            yield word + " "

    def embed(self, texts: tuple[str, ...], model: ModelConfig) -> EmbeddingResponse:
        # Stable, deterministic and cheap: enough to exercise wiring, not a real embedding.
        vectors = tuple(
            tuple(float((hash(text) >> shift) % 100) / 100 for shift in range(8)) for text in texts
        )
        return EmbeddingResponse(
            vectors=vectors,
            provider=self.name,
            model=model.model_id,
            usage=Usage(input_tokens=sum(len(text.split()) for text in texts)),
        )
