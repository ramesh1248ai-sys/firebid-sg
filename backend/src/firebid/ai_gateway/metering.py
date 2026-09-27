"""What each call cost, and what to do when a bid has spent enough.

Every model call is recorded against the bid, route, provider, model and agent run, priced
from the effective-dated prices in `llm.yaml` (FR-ADM-05). A bid carries a budget with alert
thresholds, and an agent run carries its own smaller budget: exceeding the run's budget pauses
the run and tells its owner rather than quietly spending more (NFR-15).

Money is `Decimal` throughout. Token counts multiplied by per-million prices in floating point
would drift, and this figure ends up in front of a commercial director.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from firebid.ai_gateway.config import ModelConfig
from firebid.ai_gateway.types import GenerationResponse, Usage
from firebid.db.models.ai import BidBudget
from firebid.db.models.workflow import AgentRun
from firebid.notifications import Notification, Notifier

log = structlog.get_logger("firebid.ai_gateway.metering")

PER_MILLION = Decimal(1_000_000)


class BudgetExceeded(Exception):
    """The bid or the run has spent its budget. The caller stops rather than spending more."""

    def __init__(self, scope: str, spent: Decimal, limit: Decimal) -> None:
        super().__init__(f"{scope} budget exhausted: spent {spent} of {limit} SGD")
        self.scope = scope
        self.spent = spent
        self.limit = limit


@dataclass(frozen=True)
class CallContext:
    """Who a call belongs to, for attribution and budgets."""

    bid_id: uuid.UUID | None = None
    agent: str | None = None
    run_id: uuid.UUID | None = None
    run_budget_sgd: Decimal | None = None
    owner_email: str | None = None


# The call context of the agent run in progress, set by the agent runtime around an agent's
# work so the gateway attributes every call it makes to that run and its bid without each
# agent having to pass it along (a call made outside any run has none).
_CURRENT: ContextVar[CallContext | None] = ContextVar("firebid_call_context", default=None)


@contextmanager
def calling_as(context: CallContext) -> Iterator[None]:
    token = _CURRENT.set(context)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def current_context() -> CallContext | None:
    return _CURRENT.get()


def cost_of(model: ModelConfig, usage: Usage, on: date | None = None) -> Decimal:
    """Price a call using the rate that applied on the day it was made.

    A model with no price returns zero rather than guessing: an unpriced model shows up as
    free on the admin page, which is visible and wrong in the safe direction.
    """
    price = model.price_on(on or datetime.now(UTC).date())
    if price is None:
        return Decimal(0)

    # Cached input is usually cheaper; where a provider publishes no cached rate, it is
    # charged at the ordinary input rate rather than assumed free.
    cached_rate = (
        price.cached_input_per_mtok
        if price.cached_input_per_mtok is not None
        else price.input_per_mtok
    )
    fresh_input = max(usage.input_tokens - usage.cached_input_tokens, 0)
    total = (
        Decimal(fresh_input) * Decimal(str(price.input_per_mtok))
        + Decimal(usage.output_tokens) * Decimal(str(price.output_per_mtok))
        + Decimal(usage.cached_input_tokens) * Decimal(str(cached_rate))
    ) / PER_MILLION
    return total.quantize(Decimal("0.000001"))


class Meter(Protocol):
    def check(self, context: CallContext) -> None: ...

    def record(
        self,
        route_name: str,
        model: ModelConfig,
        response: GenerationResponse,
        latency_ms: int,
        context: CallContext,
        config_version: str,
    ) -> None: ...


class NullMeter:
    """No metering. The default, so the gateway works with no database behind it."""

    def check(self, context: CallContext) -> None:
        return None

    def record(
        self,
        route_name: str,
        model: ModelConfig,
        response: GenerationResponse,
        latency_ms: int,
        context: CallContext,
        config_version: str,
    ) -> None:
        return None


class PostgresMeter:
    def __init__(
        self,
        session: Session,
        notifier: Notifier | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._notifier = notifier
        self._now = now or (lambda: datetime.now(UTC))

    # ---- reading -------------------------------------------------------------------

    def spent_on_bid(self, bid_id: uuid.UUID) -> Decimal:
        total = self._session.execute(
            select(func.coalesce(func.sum(AgentRun.cost_sgd), 0)).where(AgentRun.bid_id == bid_id)
        ).scalar_one()
        return Decimal(total or 0)

    def spent_on_run(self, run_id: uuid.UUID) -> Decimal:
        total = self._session.execute(
            select(func.coalesce(func.sum(AgentRun.cost_sgd), 0)).where(AgentRun.id == run_id)
        ).scalar_one()
        return Decimal(total or 0)

    # ---- enforcing -----------------------------------------------------------------

    def check(self, context: CallContext) -> None:
        """Refuse a call that would spend past a budget. Called before the provider is."""
        if context.run_budget_sgd is not None and context.run_id is not None:
            spent = self.spent_on_run(context.run_id)
            if spent >= context.run_budget_sgd:
                self._pause_run(context, spent)
                raise BudgetExceeded("run", spent, context.run_budget_sgd)

        if context.bid_id is None:
            return
        budget = self._budget_for(context.bid_id)
        if budget is None or budget.limit_sgd is None:
            return
        spent = self.spent_on_bid(context.bid_id)
        if spent >= Decimal(budget.limit_sgd):
            raise BudgetExceeded("bid", spent, Decimal(budget.limit_sgd))

    def _budget_for(self, bid_id: uuid.UUID) -> BidBudget | None:
        return self._session.execute(
            select(BidBudget).where(BidBudget.bid_id == bid_id)
        ).scalar_one_or_none()

    def _pause_run(self, context: CallContext, spent: Decimal) -> None:
        if context.run_id is None:
            return
        run = self._session.get(AgentRun, context.run_id)
        if run is not None and run.state == "running":
            run.state = "escalated"
            run.error_type = "run_budget_exceeded"
            run.finished_at = self._now()
        log.warning(
            "run_budget_exceeded",
            run_id=str(context.run_id),
            agent=context.agent,
            spent=str(spent),
        )
        self._notify(
            context.owner_email,
            subject=f"Agent run paused: budget of {context.run_budget_sgd} SGD reached",
            body=(
                f"The agent run {context.run_id} for {context.agent or 'an agent'} has spent "
                f"{spent} SGD and has been paused. Review it before letting it continue."
            ),
            kind="budget.run",
            bid_id=context.bid_id,
        )

    # ---- recording -----------------------------------------------------------------

    def record(
        self,
        route_name: str,
        model: ModelConfig,
        response: GenerationResponse,
        latency_ms: int,
        context: CallContext,
        config_version: str,
    ) -> None:
        # A cache hit cost nothing; recording it at full price would overstate the bill and
        # hide the saving.
        cost = Decimal(0) if response.cache_hit else cost_of(model, response.usage)

        if context.run_id is None:
            # A one-off call with no agent run around it still gets its own record.
            self._session.add(
                AgentRun(
                    bid_id=context.bid_id,
                    route=route_name,
                    agent=context.agent,
                    provider=response.provider,
                    model=response.model,
                    prompt_version=response.prompt_version,
                    config_version=config_version,
                    state="succeeded",
                    tokens_in=response.usage.input_tokens,
                    tokens_out=response.usage.output_tokens,
                    cost_sgd=cost,
                    latency_ms=latency_ms,
                    cache_hit=response.cache_hit,
                    emulated_capabilities=list(response.emulated),
                    finished_at=self._now(),
                )
            )
        else:
            # An agent run may make several calls; they accumulate onto its one record.
            self._accumulate(context, response, route_name, cost, latency_ms, config_version)
        self._session.flush()

        if context.bid_id is not None:
            self._alert_if_threshold_crossed(context.bid_id)

    def _accumulate(
        self,
        context: CallContext,
        response: GenerationResponse,
        route_name: str,
        cost: Decimal,
        latency_ms: int,
        config_version: str,
    ) -> AgentRun:
        """Update the run the agent runtime already opened, rather than making a second row."""
        run = self._session.get(AgentRun, context.run_id)
        if run is None:
            run = AgentRun(id=context.run_id, bid_id=context.bid_id, route=route_name)
            self._session.add(run)
        run.provider = response.provider
        run.model = response.model
        run.prompt_version = response.prompt_version
        run.config_version = config_version
        run.tokens_in = response.usage.input_tokens
        run.tokens_out = response.usage.output_tokens
        run.cost_sgd = (run.cost_sgd or Decimal(0)) + cost
        run.latency_ms = latency_ms
        run.cache_hit = response.cache_hit
        run.emulated_capabilities = list(response.emulated)
        return run

    def _alert_if_threshold_crossed(self, bid_id: uuid.UUID) -> None:
        budget = self._budget_for(bid_id)
        if budget is None or budget.limit_sgd is None:
            return

        limit = Decimal(budget.limit_sgd)
        if limit <= 0:
            return
        spent = self.spent_on_bid(bid_id)
        fraction = spent / limit

        already = set(budget.alerts_sent or [])
        crossed = [
            threshold
            for threshold in sorted(budget.alert_at or [])
            if Decimal(str(threshold)) <= fraction and threshold not in already
        ]
        if not crossed:
            return

        highest = crossed[-1]
        budget.alerts_sent = sorted(already | set(crossed))
        log.warning(
            "bid_budget_threshold",
            bid_id=str(bid_id),
            threshold=highest,
            spent=str(spent),
            limit=str(limit),
        )
        self._notify(
            budget.owner_email,
            subject=f"AI spend on this bid has reached {int(highest * 100)}% of its budget",
            body=(
                f"{spent} SGD of a {limit} SGD budget has been spent on AI processing for this "
                f"bid. Review the breakdown by route and model before it runs out."
            ),
            kind="budget.bid",
            bid_id=bid_id,
        )

    def _notify(
        self,
        recipient: str | None,
        *,
        subject: str,
        body: str,
        kind: str,
        bid_id: uuid.UUID | None,
    ) -> None:
        if self._notifier is None or not recipient:
            return
        self._notifier.send(
            Notification(
                recipient_email=recipient, subject=subject, body=body, kind=kind, bid_id=bid_id
            )
        )
