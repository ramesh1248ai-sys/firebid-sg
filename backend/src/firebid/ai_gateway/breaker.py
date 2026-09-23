"""A circuit breaker per provider-and-model.

When one model starts failing, the router should stop spending time and money on it and move
to the next in the chain. After a cooldown one call is let through; if it works the breaker
closes, if not the cooldown starts again.

In-process, which matches the gateway's other limits at this stage. A shared breaker moves to
PostgreSQL alongside the shared rate limiter.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class _State:
    consecutive_failures: int = 0
    opened_at: float | None = None
    half_open: bool = False


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    cooldown_seconds: float = 30.0
    clock: Callable[[], float] = time.monotonic
    _states: dict[str, _State] = field(default_factory=dict)

    def _state(self, key: str) -> _State:
        return self._states.setdefault(key, _State())

    def allows(self, key: str) -> bool:
        state = self._state(key)
        if state.opened_at is None:
            return True
        if self.clock() - state.opened_at >= self.cooldown_seconds:
            # Let exactly one call through to find out whether it has recovered.
            state.half_open = True
            return True
        return False

    def record_success(self, key: str) -> None:
        self._states[key] = _State()

    def record_failure(self, key: str) -> None:
        state = self._state(key)
        state.consecutive_failures += 1
        if state.half_open or state.consecutive_failures >= self.failure_threshold:
            state.opened_at = self.clock()
            state.half_open = False

    def snapshot(self) -> list[tuple[str, bool, int]]:
        """(key, open, consecutive failures) for everything seen, for the admin view."""
        return sorted(
            (key, self.is_open(key), state.consecutive_failures)
            for key, state in self._states.items()
        )

    def is_open(self, key: str) -> bool:
        state = self._states.get(key)
        return state is not None and state.opened_at is not None and not self.allows(key)
