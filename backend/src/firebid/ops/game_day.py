"""The provider-outage game day (NFR-03, ADR-004): the primary LLM provider goes down, and every
route either keeps working on an approved fallback or escalates cleanly to a person.

`run(config, adapters, primary)` calls each route once through the real router, with the
primary provider failing every call. Locally the other providers are fakes that answer, so
the rehearsal checks the routing and escalation logic against the real `llm.yaml`; in
staging the same code runs against the real adapters.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from firebid.ai_gateway.config import LlmConfig
from firebid.ai_gateway.errors import GatewayError, ProviderUnavailable
from firebid.ai_gateway.providers.fake import FakeAdapter
from firebid.ai_gateway.router import Router
from firebid.ai_gateway.types import GenerationRequest, Message, TextPart


class Ping(BaseModel):
    ok: bool


@dataclass
class RouteOutcome:
    route: str
    data_class: str
    outcome: str  # fallback | escalated | failed
    served_by: str | None
    detail: str


@dataclass
class GameDay:
    environment: str
    primary: str
    ran_at: str
    routes: list[RouteOutcome]

    @property
    def passed(self) -> bool:
        """Every route either fell back or escalated cleanly; none failed in another way."""
        return all(route.outcome in ("fallback", "escalated") for route in self.routes)

    def to_json(self) -> dict[str, Any]:
        fallback = sum(r.outcome == "fallback" for r in self.routes)
        escalated = sum(r.outcome == "escalated" for r in self.routes)
        return {
            "environment": self.environment,
            "primary": self.primary,
            "ran_at": self.ran_at,
            "passed": self.passed,
            "summary": (
                f"{self.environment}: primary '{self.primary}' down; {fallback} route(s) served "
                f"by an approved fallback, {escalated} escalated cleanly, "
                f"{len(self.routes) - fallback - escalated} failed"
            ),
            "routes": [asdict(route) for route in self.routes],
        }


def rehearsal_adapters(config: LlmConfig, primary: str) -> dict[str, Any]:
    """Fakes for a local rehearsal: the primary fails every call, the others answer."""
    adapters: dict[str, Any] = {}
    for name, provider in config.providers.items():
        if not provider.enabled:
            continue
        adapter = FakeAdapter(name)
        if name == primary:
            adapter.queue(
                *[ProviderUnavailable("down (game day)", provider=name) for _ in range(500)]
            )
        else:
            for _ in range(500):
                adapter.reply('{"ok": true}')
        adapters[name] = adapter
    return adapters


def run(
    config: LlmConfig, adapters: dict[str, Any], primary: str, environment: str = "local"
) -> GameDay:
    router = Router(config=config, adapters=adapters, backoff_base_seconds=0, sleep=lambda _s: None)
    outcomes = []
    for route_name, route in sorted(config.routes.items()):
        request = GenerationRequest(
            messages=(Message(role="user", parts=(TextPart("Game day: reply ok."),)),),
            output_model=Ping,
        )
        try:
            response = router.generate(route_name, request)
        except GatewayError as refusal:
            outcomes.append(
                RouteOutcome(
                    route_name, route.data_class, "escalated", None, type(refusal).__name__
                )
            )
            continue
        except Exception as error:  # anything else is a defect the game day exists to find
            outcomes.append(
                RouteOutcome(route_name, route.data_class, "failed", None, repr(error)[:200])
            )
            continue
        served = response.provider
        outcome = "fallback" if served != primary else "failed"
        outcomes.append(RouteOutcome(route_name, route.data_class, outcome, served, response.model))
    return GameDay(environment, primary, datetime.now(UTC).isoformat(), outcomes)
