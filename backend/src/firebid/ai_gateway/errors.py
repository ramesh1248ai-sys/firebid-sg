"""Gateway errors.

Adapters translate their provider's exceptions into these, so the router can decide what to
retry without knowing anything about a provider's SDK.
"""

from __future__ import annotations


class GatewayError(Exception):
    """Anything the gateway refuses or cannot complete."""


class ConfigError(GatewayError):
    """`llm.yaml` is wrong. Raised at startup, never during a call."""


class ProviderError(GatewayError):
    """A provider failed. `retryable` decides whether the router tries again or moves on."""

    retryable = False

    def __init__(self, message: str, *, provider: str, status: int | None = None) -> None:
        super().__init__(message)
        self.provider = provider
        self.status = status


class RateLimited(ProviderError):
    retryable = True


class ProviderUnavailable(ProviderError):
    """A 5xx, a timeout, or a connection failure."""

    retryable = True


class AuthenticationFailed(ProviderError):
    """Bad or missing credentials. Retrying will not help, and nor will a fallback model on
    the same provider, so the router moves straight to the next provider."""


class BadRequest(ProviderError):
    """The request was malformed for this provider. Not retryable: it will fail identically."""


class CapabilityMissing(GatewayError):
    """A request needs something the chosen model cannot do and the route forbids emulating."""


class DataClassRefused(GatewayError):
    """A provider was about to receive data it is not approved for.

    This is the gateway's last line before data leaves the building, and it is checked on
    every attempt including fallbacks, not only at startup.
    """


class NoModelAvailable(GatewayError):
    """Every model in the route's chain was refused, unavailable or exhausted.

    The agent runtime turns this into a `HumanTask` so the work is picked up by a person
    rather than silently dropped.
    """

    def __init__(self, route: str, reasons: list[str]) -> None:
        detail = "; ".join(reasons) if reasons else "no models configured"
        super().__init__(f"route '{route}': no model could serve the call ({detail})")
        self.route = route
        self.reasons = reasons


class OutputInvalid(GatewayError):
    """The model returned something that does not satisfy the route's output model."""
