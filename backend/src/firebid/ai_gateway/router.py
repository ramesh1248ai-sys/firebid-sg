"""The router: route name in, answer out, policy in between.

Order of business for every attempt, primary and fallback alike:

1. Is this provider approved for the route's data class? If not, skip it. This is checked here
   and not only at startup, because configuration can be reloaded and because a fallback must
   never be the way data reaches somewhere it was not approved to go.
2. Is the provider's circuit breaker closed?
3. Call, retrying retryable failures with backoff.

When the chain is exhausted the router raises `NoModelAvailable` rather than inventing an
answer. The agent runtime turns that into a `HumanTask`, so the work reaches a person.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import replace

import structlog

from firebid.ai_gateway.breaker import CircuitBreaker
from firebid.ai_gateway.config import LlmConfig, ModelConfig, RouteConfig, get_config
from firebid.ai_gateway.emulation import emulate
from firebid.ai_gateway.errors import (
    AuthenticationFailed,
    CapabilityMissing,
    DataClassRefused,
    NoModelAvailable,
    OutputInvalid,
    ProviderError,
)
from firebid.ai_gateway.prompts import family_of, prompt_for
from firebid.ai_gateway.providers.base import Adapter
from firebid.ai_gateway.types import (
    Attempt,
    DataClass,
    EmbeddingResponse,
    GenerationRequest,
    GenerationResponse,
    ReasoningLevel,
    StopReason,
)

log = structlog.get_logger("firebid.ai_gateway")


class Router:
    def __init__(
        self,
        config: LlmConfig | None = None,
        adapters: Mapping[str, Adapter] | None = None,
        *,
        max_attempts_per_model: int = 3,
        backoff_base_seconds: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self.config = config or get_config()
        self._adapters = dict(adapters or {})
        self.max_attempts_per_model = max_attempts_per_model
        self.backoff_base_seconds = backoff_base_seconds
        self._sleep = sleep
        self.breaker = breaker or CircuitBreaker()

    def adapter_for(self, provider_name: str) -> Adapter:
        try:
            return self._adapters[provider_name]
        except KeyError:
            raise NoModelAvailable(
                provider_name, [f"no adapter is registered for provider '{provider_name}'"]
            ) from None

    def generate(self, route_name: str, request: GenerationRequest) -> GenerationResponse:
        route = self.config.route(route_name)
        request = _apply_route_defaults(request, route.max_output_tokens, route.reasoning)

        attempts: list[Attempt] = []
        reasons: list[str] = []

        for model_name, model, provider in self.config.enabled_chain(route_name):
            key = f"{model.provider}:{model_name}"

            if not provider.approves(route.data_class):
                # Never reached with a valid config, but this is the check that actually
                # guards the data, so it runs anyway.
                reason = (
                    f"provider '{model.provider}' is not approved for '{route.data_class}' data"
                )
                attempts.append(Attempt(model.provider, model_name, ok=False, reason=reason))
                reasons.append(reason)
                log.warning(
                    "data_class_refused",
                    route=route_name,
                    provider=model.provider,
                    model=model_name,
                    data_class=str(route.data_class),
                )
                continue

            if not self.breaker.allows(key):
                reason = "circuit breaker open"
                attempts.append(Attempt(model.provider, model_name, ok=False, reason=reason))
                reasons.append(f"{model_name}: {reason}")
                continue

            adapter = self._adapters.get(model.provider)
            if adapter is None:
                reason = f"no adapter registered for '{model.provider}'"
                attempts.append(Attempt(model.provider, model_name, ok=False, reason=reason))
                reasons.append(reason)
                continue

            outcome = self._try_model(
                adapter, request, model, model_name, route_name, key, provider.kind, route
            )
            if isinstance(outcome, GenerationResponse):
                attempts.append(Attempt(model.provider, model_name, ok=True))
                return _stamp(outcome, route_name, attempts, self.config.config_hash)

            attempts.append(Attempt(model.provider, model_name, ok=False, reason=outcome))
            reasons.append(f"{model_name}: {outcome}")

        log.warning("no_model_available", route=route_name, reasons=reasons)
        raise NoModelAvailable(route_name, reasons)

    def _try_model(
        self,
        adapter: Adapter,
        request: GenerationRequest,
        model: ModelConfig,
        model_name: str,
        route_name: str,
        key: str,
        provider_kind: str,
        route: RouteConfig,
    ) -> GenerationResponse | str:
        """Return a response, or a short reason this model did not serve the call."""
        last_reason = "no attempt made"

        # The prompt is per route, optionally per model family, and its version is recorded.
        prompt = prompt_for(route.prompt or route_name, family_of(provider_kind))
        attempt_request = request
        if prompt is not None and not attempt_request.system:
            attempt_request = replace(attempt_request, system=prompt.text)

        try:
            emulation = emulate(attempt_request, model, allowed=route.allow_emulation)
        except CapabilityMissing as error:
            return str(error)
        attempt_request = emulation.request

        for attempt_number in range(1, self.max_attempts_per_model + 1):
            try:
                response = adapter.generate(attempt_request, model)
            except AuthenticationFailed as error:
                # Credentials will not fix themselves, and every model on this provider
                # shares them, so stop trying this provider.
                self.breaker.record_failure(key)
                return f"authentication failed ({error})"
            except ProviderError as error:
                last_reason = f"{type(error).__name__}: {error}"
                log.info(
                    "attempt_failed",
                    route=route_name,
                    model=model_name,
                    attempt=attempt_number,
                    retryable=error.retryable,
                    error=type(error).__name__,
                )
                if not error.retryable:
                    self.breaker.record_failure(key)
                    return last_reason
                if attempt_number < self.max_attempts_per_model:
                    self._sleep(self._backoff(attempt_number))
                    continue
                self.breaker.record_failure(key)
                return last_reason
            except OutputInvalid as error:
                # One retry on the same model, then move on: a second malformed answer is a
                # sign the model cannot hold the schema, not bad luck.
                last_reason = f"invalid output: {error}"
                if attempt_number == 1:
                    continue
                self.breaker.record_failure(key)
                return last_reason

            if response.stop_reason is StopReason.REFUSAL:
                last_reason = "the model refused the request"
                if attempt_number == 1:
                    continue
                return last_reason

            self.breaker.record_success(key)
            try:
                response = emulation.finish(response)
            except OutputInvalid as error:
                last_reason = f"invalid output: {error}"
                if attempt_number == 1:
                    continue
                return last_reason
            if prompt is not None:
                response = replace(response, prompt_version=prompt.version)
            return response

        return last_reason

    def _backoff(self, attempt_number: int) -> float:
        # Full jitter, so parallel workers do not retry in lockstep.
        ceiling = self.backoff_base_seconds * (2 ** (attempt_number - 1))
        return random.uniform(0, ceiling)  # noqa: S311  # jitter, not cryptography

    def stream(self, route_name: str, request: GenerationRequest) -> Iterator[str]:
        """Streaming uses the route's first model that is approved and available.

        It does not fall back mid-stream: once bytes have reached the caller, switching
        models would splice two different answers together.
        """
        route = self.config.route(route_name)
        request = _apply_route_defaults(request, route.max_output_tokens, route.reasoning)
        for model_name, model, provider in self.config.enabled_chain(route_name):
            if not provider.approves(route.data_class):
                continue
            key = f"{model.provider}:{model_name}"
            if not self.breaker.allows(key):
                continue
            adapter = self._adapters.get(model.provider)
            if adapter is None:
                continue
            return adapter.stream(request, model)
        raise NoModelAvailable(route_name, ["no approved, available model for streaming"])

    def embed(self, route_name: str, texts: tuple[str, ...]) -> EmbeddingResponse:
        route = self.config.route(route_name)
        reasons: list[str] = []
        for model_name, model, provider in self.config.enabled_chain(route_name):
            if not provider.approves(route.data_class):
                reasons.append(f"{model_name}: provider not approved for {route.data_class}")
                continue
            adapter = self._adapters.get(model.provider)
            if adapter is None:
                reasons.append(f"{model_name}: no adapter")
                continue
            try:
                return adapter.embed(texts, model)
            except ProviderError as error:
                reasons.append(f"{model_name}: {error}")
        raise NoModelAvailable(route_name, reasons)


def _apply_route_defaults(
    request: GenerationRequest, max_output_tokens: int, reasoning: ReasoningLevel
) -> GenerationRequest:
    """The route decides output size and reasoning depth unless the caller was explicit."""
    changes: dict[str, object] = {}
    if request.max_output_tokens == 16_000:  # the dataclass default: caller did not choose
        changes["max_output_tokens"] = max_output_tokens
    if request.reasoning is ReasoningLevel.MEDIUM:
        changes["reasoning"] = reasoning
    return replace(request, **changes) if changes else request  # type: ignore[arg-type]


def _stamp(
    response: GenerationResponse,
    route_name: str,
    attempts: list[Attempt],
    config_hash: str,
) -> GenerationResponse:
    log.info(
        "model_call",
        route=route_name,
        provider=response.provider,
        model=response.model,
        stop_reason=str(response.stop_reason),
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        attempts=len(attempts),
        config_version=config_hash,
    )
    return replace(response, route=route_name, attempts=tuple(attempts))


def refuse_unapproved(config: LlmConfig, provider_name: str, data_class: DataClass) -> None:
    """Assert a provider may receive this class. For callers outside the router."""
    provider = config.providers.get(provider_name)
    if provider is None or not provider.approves(data_class):
        raise DataClassRefused(
            f"provider '{provider_name}' is not approved for '{data_class}' data"
        )
