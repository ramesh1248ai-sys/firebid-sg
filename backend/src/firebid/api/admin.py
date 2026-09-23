"""The platform admin view: what is configured, what is healthy, and what it is costing.

Read-only. Routing comes from `llm.yaml` and is changed by editing that file, not here — a
screen that could rewrite the routing table would make the configuration two sources of truth.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from firebid.ai_gateway import gateway
from firebid.ai_gateway.config import LlmConfig, get_config
from firebid.api.deps import CurrentPrincipal, DbSession, require
from firebid.auth.permissions import Action
from firebid.db.models.core import Bid, BidMember
from firebid.db.models.workflow import AgentRun

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[require(Action.ADMIN_READ_PLATFORM)],
)


class ModelOut(BaseModel):
    name: str
    provider: str
    model_id: str
    capabilities: list[str]
    max_output_tokens: int | None


class ProviderOut(BaseModel):
    name: str
    kind: str
    platform: str
    enabled: bool
    approved_data_classes: list[str]
    # Whether a key is present, never the key itself.
    credentials_configured: bool
    retention: str | None
    no_training: bool | None


class RouteOut(BaseModel):
    name: str
    data_class: str
    requires: list[str]
    models: list[str]
    reasoning: str
    allow_emulation: bool
    # The first model whose provider is enabled: what a call would actually use now.
    effective_model: str | None


class RoutingOut(BaseModel):
    config_version: str
    providers: list[ProviderOut]
    models: list[ModelOut]
    routes: list[RouteOut]


class BreakerOut(BaseModel):
    key: str
    open: bool
    consecutive_failures: int


class SpendRow(BaseModel):
    group: str
    calls: int
    tokens_in: int
    tokens_out: int
    cost_sgd: Decimal
    cache_hits: int


@router.get("/llm/routing", response_model=RoutingOut)
def routing() -> RoutingOut:
    """Which provider and model serves each task, straight from the configuration."""
    config: LlmConfig = get_config()
    return RoutingOut(
        config_version=config.config_hash,
        providers=[
            ProviderOut(
                name=name,
                kind=provider.kind,
                platform=provider.platform,
                enabled=provider.enabled,
                approved_data_classes=[str(c) for c in provider.approved_data_classes],
                credentials_configured=provider.resolve_credential() is not None,
                retention=provider.retention,
                no_training=provider.no_training,
            )
            for name, provider in sorted(config.providers.items())
        ],
        models=[
            ModelOut(
                name=name,
                provider=model.provider,
                model_id=model.model_id,
                capabilities=[str(c) for c in model.capabilities],
                max_output_tokens=model.max_output_tokens,
            )
            for name, model in sorted(config.models.items())
        ],
        routes=[
            RouteOut(
                name=name,
                data_class=str(route.data_class),
                requires=[str(c) for c in route.requires],
                models=list(route.models),
                reasoning=str(route.reasoning),
                allow_emulation=route.allow_emulation,
                effective_model=_effective(config, name),
            )
            for name, route in sorted(config.routes.items())
        ],
    )


def _effective(config: LlmConfig, route_name: str) -> str | None:
    chain = config.enabled_chain(route_name)
    return chain[0][0] if chain else None


@router.get("/llm/health", response_model=list[BreakerOut])
def breaker_health() -> list[BreakerOut]:
    """Which models the gateway has given up on, and how close others are to that."""
    return [
        BreakerOut(key=key, open=is_open, consecutive_failures=failures)
        for key, is_open, failures in gateway().breaker.snapshot()
    ]


@router.get("/llm/cost", response_model=list[SpendRow])
def cost(
    session: DbSession,
    principal: CurrentPrincipal,
    by: Annotated[str, Query(pattern="^(bid|route|provider|model)$")] = "route",
    bid_id: uuid.UUID | None = None,
) -> list[SpendRow]:
    """What has been spent, grouped as asked (FR-ADM-05).

    Scoped to the bids this person can see, so the admin page obeys the same rule as
    everything else even for an administrator.
    """
    column = {
        "bid": AgentRun.bid_id,
        "route": AgentRun.route,
        "provider": AgentRun.provider,
        "model": AgentRun.model,
    }[by]

    statement = (
        select(
            column.label("group"),
            func.count().label("calls"),
            func.coalesce(func.sum(AgentRun.tokens_in), 0).label("tokens_in"),
            func.coalesce(func.sum(AgentRun.tokens_out), 0).label("tokens_out"),
            func.coalesce(func.sum(AgentRun.cost_sgd), 0).label("cost_sgd"),
            func.count().filter(AgentRun.cache_hit.is_(True)).label("cache_hits"),
        )
        .group_by(column)
        .order_by(func.coalesce(func.sum(AgentRun.cost_sgd), 0).desc())
    )
    if bid_id is not None:
        statement = statement.where(AgentRun.bid_id == bid_id)
    statement = statement.where(
        AgentRun.bid_id.in_(select(BidMember.bid_id).where(BidMember.user_id == principal.user_id))
        | AgentRun.bid_id.is_(None)
    )

    rows = session.execute(statement).all()
    return [
        SpendRow(
            group=_label(session, by, row.group),
            calls=row.calls,
            tokens_in=row.tokens_in,
            tokens_out=row.tokens_out,
            cost_sgd=Decimal(row.cost_sgd),
            cache_hits=row.cache_hits,
        )
        for row in rows
    ]


def _label(session: DbSession, by: str, value: object) -> str:
    """A bid reads as its human identifier, not a UUID nobody recognises."""
    if value is None:
        return "(none)"
    if by != "bid":
        return str(value)
    bid = session.get(Bid, value)
    return bid.human_id if bid else str(value)
