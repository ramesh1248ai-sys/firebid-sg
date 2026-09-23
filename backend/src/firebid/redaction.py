"""Keeping tender content and prices out of logs and traces (NFR-14, guardrail 8).

Observability is supposed to tell you what happened, not what was in the drawing. A log line
carrying a clause of a client's specification, or a supplier's rate, has taken confidential
data out of the database and put it somewhere with weaker access control and longer retention.

So this is deliberately blunt. Anything that looks like document text or money is replaced
before it reaches a log line or a span, and the allowed keys are listed rather than the
forbidden ones: a new field is redacted by default, which is the safe direction for a list
nobody will remember to update.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, MutableMapping
from typing import Any

REDACTED = "[redacted]"

# Keys that may carry their value through. Everything else is summarised, not printed.
SAFE_KEYS = frozenset(
    {
        "event",
        "level",
        "logger",
        "timestamp",
        "request_id",
        "route",
        "provider",
        "model",
        "agent",
        "attempt",
        "attempts",
        "retryable",
        "error",
        "error_type",
        "stop_reason",
        "config_version",
        "prompt_version",
        "data_class",
        "bid_id",
        "run_id",
        "sheet_id",
        "document_id",
        "job_id",
        "count",
        "duration_ms",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "tokens",
        "cache_hit",
        "emulated",
        "status",
        "path",
        "method",
        "limit",
        "threshold",
        "bucket",
        "reason",
        "reasons",
        "partitions",
        "user_id",
        "username",
        "age_seconds",
        "last_heartbeat",
        "declared",
        "received",
        "entity_type",
        "entity_id",
        "action",
        "chain_key",
        "deadline_kind",
        "days_before",
        "recipients",
        "kind",
        "subject",
        "state",
        "spent",
        "months_ahead",
        "reason_code",
        "exc_info",
        "stack",
        "exception",
    }
)

# Values that are money, wherever they appear.
MONEY = re.compile(r"(?:SGD|S\$|\$)\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*\.\d{2}\b")


def scrub(key: str, value: Any) -> Any:
    """What may be logged under this key."""
    if key in SAFE_KEYS:
        return _scrub_money(value) if isinstance(value, str) else value
    if isinstance(value, bool | int | float) or value is None:
        return value
    if isinstance(value, str):
        # Deliberately strict: a short string under an unknown key could be a supplier's
        # name, a sheet number or a person. Allow the key if the value is needed.
        return f"{REDACTED} ({len(value)} characters)"
    if isinstance(value, bytes):
        return f"{REDACTED} ({len(value)} bytes)"
    if isinstance(value, dict | list | tuple | set):
        return f"{REDACTED} ({type(value).__name__}, {len(value)} items)"
    return REDACTED


def _scrub_money(text: str) -> str:
    return MONEY.sub(REDACTED, text)


def redact_processor(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    """structlog processor. Sits last, so nothing added later escapes it."""
    return {key: scrub(key, value) for key, value in event_dict.items()}


def span_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    """The same rule for trace attributes, which are exported to third parties."""
    scrubbed: dict[str, Any] = {}
    for key, value in attributes.items():
        cleaned = scrub(key, value)
        # OpenTelemetry only accepts primitives.
        scrubbed[key] = cleaned if isinstance(cleaned, str | bool | int | float) else str(cleaned)
    return scrubbed
