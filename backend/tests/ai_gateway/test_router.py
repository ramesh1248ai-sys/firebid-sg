"""Router policy: fallback, breakers, and the rule that data never reaches an
unapproved provider."""

from __future__ import annotations

from pathlib import Path

import pytest

from firebid.ai_gateway.breaker import CircuitBreaker
from firebid.ai_gateway.config import load_config
from firebid.ai_gateway.errors import (
    AuthenticationFailed,
    BadRequest,
    NoModelAvailable,
    ProviderUnavailable,
    RateLimited,
)
from firebid.ai_gateway.providers.fake import FakeAdapter
from firebid.ai_gateway.router import Router
from firebid.ai_gateway.types import GenerationRequest, Message, StopReason

CONFIG = """
version: 1
providers:
  primary:
    kind: fake
    approved_data_classes: [internal, confidential]
  secondary:
    kind: fake
    approved_data_classes: [internal, confidential]
  internal_only:
    kind: fake
    approved_data_classes: [internal]
models:
  first:
    provider: primary
    model_id: first-1
    capabilities: [structured_output]
  second:
    provider: secondary
    model_id: second-1
    capabilities: [structured_output]
  unapproved:
    provider: internal_only
    model_id: internal-1
    capabilities: [structured_output]
routes:
  work:
    requires: [structured_output]
    data_class: confidential
    models: [first, second]
    reasoning: low
    max_output_tokens: 500
"""


@pytest.fixture
def config(tmp_path: Path):  # type: ignore[no-untyped-def]
    path = tmp_path / "llm.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    return load_config(path)


def router(config, **adapters: FakeAdapter) -> Router:  # type: ignore[no-untyped-def]
    return Router(
        config=config,
        adapters=adapters,
        backoff_base_seconds=0,
        sleep=lambda _seconds: None,
    )


def ask() -> GenerationRequest:
    return GenerationRequest(messages=(Message.user("hello"),))


@pytest.mark.req("NFR-05")
class TestFallback:
    def test_the_primary_serves_the_call_when_it_works(self, config) -> None:  # type: ignore[no-untyped-def]
        primary, secondary = FakeAdapter("primary").reply("from primary"), FakeAdapter("secondary")
        response = router(config, primary=primary, secondary=secondary).generate("work", ask())
        assert response.text == "from primary"
        assert secondary.calls == []

    @pytest.mark.parametrize(
        "failure",
        [
            ProviderUnavailable("503", provider="primary", status=503),
            RateLimited("429", provider="primary", status=429),
            ProviderUnavailable("timed out", provider="primary"),
        ],
        ids=["server-error", "rate-limit", "timeout"],
    )
    def test_a_retryable_failure_falls_through_to_the_next_model(self, config, failure) -> None:  # type: ignore[no-untyped-def]
        primary = FakeAdapter("primary").queue(failure, failure, failure)
        secondary = FakeAdapter("secondary").reply("from secondary")
        response = router(config, primary=primary, secondary=secondary).generate("work", ask())
        assert response.text == "from secondary"
        assert response.model == "second-1"
        # The fallback is recorded, with why, so a span or log line can explain it.
        assert [(a.model, a.ok) for a in response.attempts] == [("first", False), ("second", True)]
        assert response.attempts[0].reason is not None

    def test_a_retryable_failure_is_retried_before_falling_back(self, config) -> None:  # type: ignore[no-untyped-def]
        primary = FakeAdapter("primary").queue(
            RateLimited("429", provider="primary"), None or _ok("recovered")
        )
        response = router(config, primary=primary, secondary=FakeAdapter("secondary")).generate(
            "work", ask()
        )
        assert response.text == "recovered"
        assert len(primary.calls) == 2

    def test_a_non_retryable_failure_moves_on_without_retrying(self, config) -> None:  # type: ignore[no-untyped-def]
        primary = FakeAdapter("primary").queue(BadRequest("malformed", provider="primary"))
        secondary = FakeAdapter("secondary").reply("from secondary")
        response = router(config, primary=primary, secondary=secondary).generate("work", ask())
        assert response.text == "from secondary"
        assert len(primary.calls) == 1  # tried once, not three times

    def test_bad_credentials_abandon_that_provider_at_once(self, config) -> None:  # type: ignore[no-untyped-def]
        primary = FakeAdapter("primary").queue(AuthenticationFailed("401", provider="primary"))
        secondary = FakeAdapter("secondary").reply("from secondary")
        response = router(config, primary=primary, secondary=secondary).generate("work", ask())
        assert response.text == "from secondary"
        assert len(primary.calls) == 1

    def test_when_every_model_fails_the_call_escalates(self, config) -> None:  # type: ignore[no-untyped-def]
        """No invented answer: the agent runtime turns this into a HumanTask."""
        failure = ProviderUnavailable("503", provider="x", status=503)
        both = {
            "primary": FakeAdapter("primary").queue(failure, failure, failure),
            "secondary": FakeAdapter("secondary").queue(failure, failure, failure),
        }
        with pytest.raises(NoModelAvailable) as caught:
            router(config, **both).generate("work", ask())
        assert caught.value.route == "work"
        assert len(caught.value.reasons) == 2


def _ok(text: str):  # type: ignore[no-untyped-def]
    from firebid.ai_gateway.types import GenerationResponse

    return GenerationResponse(
        text=text, stop_reason=StopReason.END, provider="primary", model="first-1"
    )


@pytest.mark.req("NFR-08")
class TestDataClassIsEnforcedOnEveryAttempt:
    def test_an_unapproved_provider_is_skipped_even_as_a_fallback(self, tmp_path: Path) -> None:
        """The whole point: a fallback must not become the way data escapes its approval."""
        text = CONFIG.replace("    models: [first, second]", "    models: [first, unapproved]")
        path = tmp_path / "llm.yaml"
        path.write_text(text, encoding="utf-8")
        # Startup would normally refuse this, so build the object past the validator to prove
        # the runtime check stands on its own.
        config = load_config(
            path,
            overlay={
                "routes": {
                    "work": {
                        "requires": ["structured_output"],
                        "data_class": "internal",
                        "models": ["first", "unapproved"],
                    }
                }
            },
        )
        object.__setattr__(config.routes["work"], "data_class", "confidential")

        failure = ProviderUnavailable("503", provider="primary", status=503)
        primary = FakeAdapter("primary").queue(failure, failure, failure)
        unapproved = FakeAdapter("internal_only").reply("should never be reached")

        with pytest.raises(NoModelAvailable) as caught:
            router(config, primary=primary, internal_only=unapproved).generate("work", ask())

        assert unapproved.calls == [], "confidential data reached an unapproved provider"
        assert any("not approved" in reason for reason in caught.value.reasons)

    def test_startup_would_have_refused_that_configuration_anyway(self, tmp_path: Path) -> None:
        from firebid.ai_gateway.errors import ConfigError

        text = CONFIG.replace("    models: [first, second]", "    models: [first, unapproved]")
        path = tmp_path / "llm.yaml"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ConfigError, match="approved only for"):
            load_config(path)


@pytest.mark.req("NFR-05")
class TestCircuitBreaker:
    def test_it_opens_after_repeated_failures_and_stops_calling(self, config) -> None:  # type: ignore[no-untyped-def]
        clock = _Clock()
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=30, clock=clock)
        failure = ProviderUnavailable("503", provider="primary", status=503)
        primary = FakeAdapter("primary").queue(*[failure] * 9)
        secondary = FakeAdapter("secondary").reply("a").reply("b")
        gateway = Router(
            config=config,
            adapters={"primary": primary, "secondary": secondary},
            backoff_base_seconds=0,
            sleep=lambda _s: None,
            breaker=breaker,
        )

        gateway.generate("work", ask())
        calls_after_first = len(primary.calls)
        gateway.generate("work", ask())
        assert len(primary.calls) == calls_after_first, "breaker did not stop the failing model"

    def test_it_lets_one_call_through_after_the_cooldown(self, config) -> None:  # type: ignore[no-untyped-def]
        clock = _Clock()
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=30, clock=clock)
        breaker.record_failure("primary:first")
        assert breaker.allows("primary:first") is False
        clock.advance(31)
        assert breaker.allows("primary:first") is True

    def test_a_success_closes_it_again(self) -> None:
        breaker = CircuitBreaker(failure_threshold=2)
        breaker.record_failure("k")
        breaker.record_success("k")
        breaker.record_failure("k")
        assert breaker.allows("k") is True


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        return self.now


@pytest.mark.req("NFR-11")
class TestSwitchingProviderIsConfigurationOnly:
    def test_the_same_call_runs_on_either_provider_and_records_which(self, tmp_path: Path) -> None:
        """The headline promise: change the chain in llm.yaml, change nothing in code."""
        path = tmp_path / "llm.yaml"
        request = ask()
        results = {}

        for first_choice in ("first", "second"):
            other = "second" if first_choice == "first" else "first"
            path.write_text(
                CONFIG.replace(
                    "    models: [first, second]", f"    models: [{first_choice}, {other}]"
                ),
                encoding="utf-8",
            )
            config = load_config(path)
            adapters = {
                "primary": FakeAdapter("primary").reply("answer", model="first-1"),
                "secondary": FakeAdapter("secondary").reply("answer", model="second-1"),
            }
            response = router(config, **adapters).generate("work", request)
            results[first_choice] = response

        assert results["first"].text == results["second"].text == "answer"
        assert results["first"].model == "first-1"
        assert results["second"].model == "second-1"
        assert results["first"].provider != results["second"].provider

    def test_the_route_supplies_defaults_the_caller_did_not_set(self, config) -> None:  # type: ignore[no-untyped-def]
        primary = FakeAdapter("primary")
        router(config, primary=primary).generate("work", ask())
        _model_id, sent = primary.calls[0]
        assert sent.max_output_tokens == 500  # from the route, not the dataclass default
        assert str(sent.reasoning) == "low"

    def test_every_answer_records_the_route_it_came_from(self, config) -> None:  # type: ignore[no-untyped-def]
        response = router(config, primary=FakeAdapter("primary").reply("x")).generate("work", ask())
        assert response.route == "work"
        assert response.attempts[-1].ok is True


@pytest.mark.req("NFR-05")
class TestRefusalAndBadOutput:
    def test_a_refusal_is_retried_once_then_falls_back(self, config) -> None:  # type: ignore[no-untyped-def]
        from firebid.ai_gateway.types import GenerationResponse

        refusal = GenerationResponse(
            text="", stop_reason=StopReason.REFUSAL, provider="primary", model="first-1"
        )
        primary = FakeAdapter("primary").queue(refusal, refusal)
        secondary = FakeAdapter("secondary").reply("from secondary")
        response = router(config, primary=primary, secondary=secondary).generate("work", ask())
        assert response.text == "from secondary"
        assert len(primary.calls) == 2  # tried twice on the same model before moving on

    def test_output_that_fails_the_schema_is_retried_once_then_falls_back(self, config) -> None:  # type: ignore[no-untyped-def]
        from pydantic import BaseModel

        class Shape(BaseModel):
            value: int

        primary = FakeAdapter("primary").reply("not json").reply("still not json")
        secondary = FakeAdapter("secondary").reply('{"value": 7}')
        request = GenerationRequest(messages=(Message.user("x"),), output_model=Shape)
        response = router(config, primary=primary, secondary=secondary).generate("work", request)
        assert response.parsed == Shape(value=7)
        assert len(primary.calls) == 2
