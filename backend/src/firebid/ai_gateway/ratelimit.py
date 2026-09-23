"""A rate limiter every worker process shares.

An in-process limiter is worse than none when the work is spread across worker processes:
each one believes it has the whole budget, and together they sail past the provider's limit
and collect 429s. So the budget lives in PostgreSQL, which every worker already talks to.

A fixed window per minute, incremented atomically. The increment only happens when the window
still has room, so two workers cannot both believe they took the last slot.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

log = structlog.get_logger("firebid.ai_gateway.ratelimit")

# Increment only if this window is still under the limit. `RETURNING` tells us which happened:
# a row back means we took a slot, nothing back means the window is full.
CLAIM = text(
    """
    INSERT INTO llm_rate_bucket (id, bucket_key, window_start, requests, tokens)
    VALUES (gen_random_uuid(), :key, :window_start, 1, :tokens)
    ON CONFLICT (bucket_key, window_start) DO UPDATE
        SET requests = llm_rate_bucket.requests + 1,
            tokens = llm_rate_bucket.tokens + :tokens
        WHERE llm_rate_bucket.requests < :limit
    RETURNING requests
    """
)


class RateLimiter(Protocol):
    def acquire(self, key: str, limit: int | None, tokens: int = 0) -> bool: ...


class NullRateLimiter:
    """No limiting. The default, so the gateway works with no database behind it."""

    def acquire(self, key: str, limit: int | None, tokens: int = 0) -> bool:
        return True


class PostgresRateLimiter:
    """Waits for capacity up to `timeout_seconds`, then gives up.

    Giving up is reported to the router as a rate-limit failure, so the call falls back to the
    next model in the chain rather than queueing behind a saturated provider.
    """

    def __init__(
        self,
        session: Session,
        timeout_seconds: float = 5.0,
        poll_seconds: float = 0.25,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._session = session
        self._timeout = timeout_seconds
        self._poll = poll_seconds
        self._now = now or (lambda: datetime.now(UTC))
        self._sleep = sleep

    def _window(self) -> datetime:
        moment = self._now()
        return moment.replace(second=0, microsecond=0)

    def _claim(self, key: str, limit: int, tokens: int) -> bool:
        result = self._session.execute(
            CLAIM,
            {"key": key, "window_start": self._window(), "tokens": tokens, "limit": limit},
        ).first()
        # The claim must be visible to other workers immediately, not at the end of whatever
        # transaction this call happens to sit in.
        self._session.commit()
        return result is not None

    def acquire(self, key: str, limit: int | None, tokens: int = 0) -> bool:
        if limit is None:
            return True

        deadline = time.monotonic() + self._timeout
        while True:
            if self._claim(key, limit, tokens):
                return True
            if time.monotonic() >= deadline:
                log.warning("rate_limit_timeout", bucket=key, limit=limit)
                return False
            self._sleep(self._poll)
