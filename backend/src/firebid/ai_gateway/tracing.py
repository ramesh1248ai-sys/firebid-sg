"""OpenTelemetry spans for model calls (NFR-14).

One span per attempt, carrying route, provider, model, attempt number, the reason a fallback
happened, and the configuration version — enough to answer "why did this call go to that
model and what did it cost" from the trace alone, without any of the content.

Every attribute goes through the same redaction as the logs. Spans are usually exported to a
third-party backend, so they are the last place tender text should end up.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from firebid.redaction import span_attributes

tracer = trace.get_tracer("firebid.ai_gateway")


@contextmanager
def model_attempt(
    route: str,
    provider: str,
    model: str,
    attempt: int,
    config_version: str,
    **extra: Any,
) -> Iterator[Span]:
    """One attempt against one model."""
    attributes = span_attributes(
        {
            "route": route,
            "provider": provider,
            "model": model,
            "attempt": attempt,
            "config_version": config_version,
            **extra,
        }
    )
    with tracer.start_as_current_span(
        f"llm.attempt {route}", kind=SpanKind.CLIENT, attributes=attributes
    ) as span:
        yield span


@contextmanager
def tool_call(name: str, route: str) -> Iterator[Span]:
    """One tool call made inside a model turn. The arguments are never recorded."""
    with tracer.start_as_current_span(
        f"llm.tool {name}",
        kind=SpanKind.INTERNAL,
        attributes=span_attributes({"tool": name, "route": route}),
    ) as span:
        yield span


def record_outcome(span: Span, *, ok: bool, reason: str | None = None, **extra: Any) -> None:
    """Close out an attempt: why it failed, or what it used."""
    for key, value in span_attributes(dict(extra)).items():
        span.set_attribute(key, value)
    if ok:
        span.set_status(Status(StatusCode.OK))
        return
    span.set_status(Status(StatusCode.ERROR, reason or "attempt failed"))
    if reason:
        span.set_attribute("fallback_reason", span_attributes({"reason": reason})["reason"])


def usage_attributes(usage: Mapping[str, int], cost_sgd: str | None = None) -> dict[str, Any]:
    attributes: dict[str, Any] = {f"usage.{key}": value for key, value in usage.items()}
    if cost_sgd is not None:
        # A cost figure is not a price from a supplier quotation, so it may be recorded.
        attributes["cost_sgd"] = cost_sgd
    return attributes
