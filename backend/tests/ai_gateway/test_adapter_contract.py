"""One suite every adapter must pass.

The provider SDKs are replaced by stubs that return the shapes those SDKs return, so what is
under test is our translation — request mapping, usage, stop reasons and error classification
— rather than the provider's servers. Live calls are a separate, credential-gated test below.

When a new provider is added, it joins `ADAPTERS` and must pass all of this unchanged.
"""

from __future__ import annotations

from typing import Any, Literal

import pytest
from pydantic import BaseModel

from firebid.ai_gateway.config import ModelConfig, ProviderConfig
from firebid.ai_gateway.errors import (
    AuthenticationFailed,
    BadRequest,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
)
from firebid.ai_gateway.types import (
    GenerationRequest,
    ImagePart,
    Message,
    StopReason,
    TextPart,
    ToolDef,
    Usage,
)

pytestmark = pytest.mark.req("NFR-05")

PNG = b"\x89PNG\r\n\x1a\n"


class TitleBlock(BaseModel):
    sheet_number: str
    revision: str


def request(**changes: Any) -> GenerationRequest:
    base: dict[str, Any] = {"messages": (Message.user("read this"),)}
    base.update(changes)
    return GenerationRequest(**base)


# --------------------------------------------------------------------------------------
# Stub SDK clients: the shapes each provider's SDK actually returns.
# --------------------------------------------------------------------------------------


class Box:
    """A tiny attribute bag, so stubs read like the SDK objects they stand in for."""

    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)


class StubAnthropic:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.captured: dict[str, Any] = {}
        self._response = response
        self._error = error
        self.messages = Box(create=self._create, parse=self._create, stream=self._stream)

    def _create(self, **payload: Any) -> Any:
        self.captured = payload
        if self._error:
            raise self._error
        return self._response or Box(
            id="msg_1",
            content=[Box(type="text", text="ok")],
            stop_reason="end_turn",
            usage=Box(input_tokens=11, output_tokens=7, cache_read_input_tokens=3),
        )

    def _stream(self, **payload: Any) -> Any:
        self.captured = payload
        return _StubStream(["a", "b"])


class _StubStream:
    def __init__(self, pieces: list[str]) -> None:
        self.text_stream = iter(pieces)

    def __enter__(self) -> _StubStream:
        return self

    def __exit__(self, *_: object) -> Literal[False]:
        return False


class StubOpenAI:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.captured: dict[str, Any] = {}
        self._response = response
        self._error = error
        self.chat = Box(completions=Box(create=self._create))
        self.embeddings = Box(create=self._embed)

    def _create(self, **payload: Any) -> Any:
        self.captured = payload
        if self._error:
            raise self._error
        if payload.get("stream"):
            # OpenAI streams through the same call, returning chunks rather than a message.
            return iter(
                [
                    Box(choices=[Box(delta=Box(content="a"))]),
                    Box(choices=[Box(delta=Box(content="b"))]),
                ]
            )
        return self._response or Box(
            id="chatcmpl-1",
            choices=[Box(message=Box(content="ok", tool_calls=None), finish_reason="stop")],
            usage=Box(
                prompt_tokens=11, completion_tokens=7, prompt_tokens_details=Box(cached_tokens=3)
            ),
        )

    def _embed(self, **payload: Any) -> Any:
        self.captured = payload
        return Box(data=[Box(embedding=[0.1, 0.2])], usage=Box(prompt_tokens=4))


class StubGoogle:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.captured: dict[str, Any] = {}
        self._response = response
        self._error = error
        self.models = Box(
            generate_content=self._generate,
            generate_content_stream=self._stream,
            embed_content=self._embed,
        )

    def _generate(self, **payload: Any) -> Any:
        self.captured = payload
        if self._error:
            raise self._error
        return self._response or Box(
            response_id="resp-1",
            text="ok",
            function_calls=None,
            candidates=[Box(finish_reason="FinishReason.STOP")],
            usage_metadata=Box(
                prompt_token_count=11, candidates_token_count=7, cached_content_token_count=3
            ),
        )

    def _stream(self, **payload: Any) -> Any:
        self.captured = payload
        return iter([Box(text="a"), Box(text="b")])

    def _embed(self, **payload: Any) -> Any:
        self.captured = payload
        return Box(embeddings=[Box(values=[0.1, 0.2])])


# --------------------------------------------------------------------------------------
# Building each adapter over its stub.
# --------------------------------------------------------------------------------------


def anthropic_adapter(stub: Any) -> Any:
    from firebid.ai_gateway.providers.anthropic_provider import AnthropicAdapter

    return AnthropicAdapter("anthropic", ProviderConfig(kind="anthropic"), client=stub)


def openai_adapter(stub: Any) -> Any:
    from firebid.ai_gateway.providers.openai_provider import OpenAIAdapter

    return OpenAIAdapter("openai", ProviderConfig(kind="openai"), client=stub)


def google_adapter(stub: Any) -> Any:
    from firebid.ai_gateway.providers.google_provider import GoogleAdapter

    return GoogleAdapter("google", ProviderConfig(kind="google"), client=stub)


ADAPTERS = [
    pytest.param(StubAnthropic, anthropic_adapter, id="anthropic"),
    pytest.param(StubOpenAI, openai_adapter, id="openai"),
    pytest.param(StubGoogle, google_adapter, id="google"),
]

MODEL = ModelConfig(provider="p", model_id="test-model-1", max_output_tokens=4096)


@pytest.mark.parametrize(("stub_class", "build"), ADAPTERS)
class TestEveryAdapter:
    def test_generates_text(self, stub_class: type, build: Any) -> None:
        response = build(stub_class()).generate(request(), MODEL)
        assert response.text == "ok"
        assert response.model == "test-model-1"
        assert response.stop_reason is StopReason.END

    def test_maps_usage(self, stub_class: type, build: Any) -> None:
        """Cost attribution is only as good as this mapping."""
        response = build(stub_class()).generate(request(), MODEL)
        assert response.usage == Usage(input_tokens=11, output_tokens=7, cached_input_tokens=3)

    def test_keeps_the_provider_request_id_for_support(self, stub_class: type, build: Any) -> None:
        assert build(stub_class()).generate(request(), MODEL).raw_request_id

    def test_sends_the_model_id_the_configuration_names(self, stub_class: type, build: Any) -> None:
        stub = stub_class()
        build(stub).generate(request(), MODEL)
        assert stub.captured["model"] == "test-model-1"

    def test_caps_output_at_the_model_limit(self, stub_class: type, build: Any) -> None:
        stub = stub_class()
        build(stub).generate(request(max_output_tokens=999_999), MODEL)
        sent = stub.captured
        cap = (
            sent.get("max_tokens")
            or sent.get("max_completion_tokens")
            or sent["config"]["max_output_tokens"]
        )
        assert cap == 4096

    def test_accepts_an_image(self, stub_class: type, build: Any) -> None:
        message = Message(role="user", parts=(ImagePart("image/png", PNG), TextPart("what?")))
        response = build(stub_class()).generate(request(messages=(message,)), MODEL)
        assert response.text == "ok"

    def test_declares_tools_when_asked(self, stub_class: type, build: Any) -> None:
        stub = stub_class()
        tool = ToolDef(name="lookup", description="look it up", input_schema={"type": "object"})
        build(stub).generate(request(tools=(tool,)), MODEL)
        assert "lookup" in str(stub.captured)

    def test_validates_structured_output_against_the_route_model(
        self, stub_class: type, build: Any
    ) -> None:
        good = '{"sheet_number": "FP-L05-201", "revision": "R04"}'
        stub = stub_class()
        _set_text(stub, good)
        response = build(stub).generate(request(output_model=TitleBlock), MODEL)
        assert isinstance(response.parsed, TitleBlock)
        assert response.parsed.sheet_number == "FP-L05-201"

    def test_rejects_output_that_does_not_match(self, stub_class: type, build: Any) -> None:
        """A provider that ignores the schema must not slip past the gateway."""
        from firebid.ai_gateway.errors import OutputInvalid

        stub = stub_class()
        _set_text(stub, '{"sheet_number": "FP-L05-201"}')  # revision missing
        with pytest.raises(OutputInvalid):
            build(stub).generate(request(output_model=TitleBlock), MODEL)

    def test_streams(self, stub_class: type, build: Any) -> None:
        assert "".join(build(stub_class()).stream(request(), MODEL)) == "ab"

    def test_reports_a_truncated_answer(self, stub_class: type, build: Any) -> None:
        stub = stub_class()
        _set_stop(stub, anthropic="max_tokens", openai="length", google="FinishReason.MAX_TOKENS")
        assert build(stub).generate(request(), MODEL).stop_reason is StopReason.MAX_OUTPUT


def _set_text(stub: Any, text: str) -> None:
    """Point a stub at a specific body, in whichever shape its SDK uses."""
    if isinstance(stub, StubAnthropic):
        stub._response = Box(
            id="msg_1",
            content=[Box(type="text", text=text)],
            stop_reason="end_turn",
            usage=Box(input_tokens=11, output_tokens=7, cache_read_input_tokens=3),
            parsed_output=None,
        )
    elif isinstance(stub, StubOpenAI):
        stub._response = Box(
            id="chatcmpl-1",
            choices=[Box(message=Box(content=text, tool_calls=None), finish_reason="stop")],
            usage=Box(prompt_tokens=11, completion_tokens=7, prompt_tokens_details=None),
        )
    else:
        stub._response = Box(
            response_id="resp-1",
            text=text,
            function_calls=None,
            candidates=[Box(finish_reason="FinishReason.STOP")],
            usage_metadata=Box(
                prompt_token_count=11, candidates_token_count=7, cached_content_token_count=3
            ),
        )


def _set_stop(stub: Any, *, anthropic: str, openai: str, google: str) -> None:
    if isinstance(stub, StubAnthropic):
        stub._response = Box(
            id="msg_1",
            content=[Box(type="text", text="ok")],
            stop_reason=anthropic,
            usage=Box(input_tokens=11, output_tokens=7, cache_read_input_tokens=3),
        )
    elif isinstance(stub, StubOpenAI):
        stub._response = Box(
            id="chatcmpl-1",
            choices=[Box(message=Box(content="ok", tool_calls=None), finish_reason=openai)],
            usage=Box(prompt_tokens=11, completion_tokens=7, prompt_tokens_details=None),
        )
    else:
        stub._response = Box(
            response_id="resp-1",
            text="ok",
            function_calls=None,
            candidates=[Box(finish_reason=google)],
            usage_metadata=Box(
                prompt_token_count=11, candidates_token_count=7, cached_content_token_count=3
            ),
        )


class TestErrorsAreClassifiedForTheRouter:
    """The router retries on `retryable`, so a misclassified error is a real fault."""

    def test_anthropic(self) -> None:
        import anthropic
        import httpx2

        request_ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        cases: list[tuple[Exception, type[ProviderError], bool]] = [
            (
                anthropic.RateLimitError(
                    "slow down", response=httpx2.Response(429, request=request_), body=None
                ),
                RateLimited,
                True,
            ),
            (
                anthropic.AuthenticationError(
                    "bad key", response=httpx2.Response(401, request=request_), body=None
                ),
                AuthenticationFailed,
                False,
            ),
            (
                anthropic.BadRequestError(
                    "nope", response=httpx2.Response(400, request=request_), body=None
                ),
                BadRequest,
                False,
            ),
            (anthropic.APIConnectionError(request=request_), ProviderUnavailable, True),
        ]
        for raised, expected, retryable in cases:
            adapter = anthropic_adapter(StubAnthropic(error=raised))
            with pytest.raises(expected) as caught:
                adapter.generate(request(), MODEL)
            assert caught.value.retryable is retryable

    def test_openai(self) -> None:
        import httpx2
        import openai

        request_ = httpx2.Request("POST", "https://api.openai.com/v1/chat/completions")
        cases: list[tuple[Exception, type[ProviderError], bool]] = [
            (
                openai.RateLimitError(
                    "slow down", response=httpx2.Response(429, request=request_), body=None
                ),
                RateLimited,
                True,
            ),
            (
                openai.AuthenticationError(
                    "bad key", response=httpx2.Response(401, request=request_), body=None
                ),
                AuthenticationFailed,
                False,
            ),
            (
                openai.BadRequestError(
                    "nope", response=httpx2.Response(400, request=request_), body=None
                ),
                BadRequest,
                False,
            ),
            (openai.APIConnectionError(request=request_), ProviderUnavailable, True),
        ]
        for raised, expected, retryable in cases:
            adapter = openai_adapter(StubOpenAI(error=raised))
            with pytest.raises(expected) as caught:
                adapter.generate(request(), MODEL)
            assert caught.value.retryable is retryable

    def test_google(self) -> None:
        from google.genai import errors as genai_errors

        google_cases: list[tuple[int, type[ProviderError], bool]] = [
            (429, RateLimited, True),
            (401, AuthenticationFailed, False),
            (400, BadRequest, False),
            (503, ProviderUnavailable, True),
        ]
        for status, expected, retryable in google_cases:
            raised = genai_errors.APIError(status, {"error": {"message": "x"}})
            adapter = google_adapter(StubGoogle(error=raised))
            with pytest.raises(expected) as caught:
                adapter.generate(request(), MODEL)
            assert caught.value.retryable is retryable
