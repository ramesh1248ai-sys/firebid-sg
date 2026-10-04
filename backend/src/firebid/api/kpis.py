"""Phase 1 KPIs and AI cost, for the dashboard (requirements §14, NFR-14, NFR-15; P1-11).

Every bid the signed-in person can see (row-level security decides), with what it measures
on its own, and the AI cost per bid by route, provider and model against its budget and the
target cost per tender. Accuracy against verified truth comes from `firebid-eval exit`.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from firebid.api.deps import CurrentBid, CurrentPrincipal, DbSession
from firebid.db.models.core import Bid
from firebid.services import kpis

router = APIRouter(tags=["kpis"])


class CostLine(BaseModel):
    route: str
    provider: str
    model: str
    runs: int
    cost_sgd: Decimal


class BidKpisOut(BaseModel):
    bid_id: uuid.UUID
    human_id: str
    ai_items: int
    verified_items: int
    false_detection_rate: float | None
    missed_item_rate: float | None
    duplicate_groups: int
    unresolved_duplicates: int
    minutes_on_task: int
    agent_runs: int
    escalation_rate: float | None
    success_rate: float | None
    cost_sgd: Decimal
    budget_sgd: Decimal | None
    target_cost_sgd: Decimal | None
    cost_by_model: list[CostLine]


class KpisOut(BaseModel):
    bids: list[BidKpisOut]
    agent_runs: int
    escalation_rate: float | None
    success_rate: float | None
    cost_sgd: Decimal
    target_cost_sgd: Decimal | None
    targets: dict[str, Any]


def bid_out(found: kpis.BidKpis) -> BidKpisOut:
    return BidKpisOut(
        bid_id=found.bid_id,
        human_id=found.human_id,
        ai_items=found.ai_items,
        verified_items=found.verified_items,
        false_detection_rate=found.false_detection_rate,
        missed_item_rate=found.missed_item_rate,
        duplicate_groups=found.duplicate_groups,
        unresolved_duplicates=found.unresolved_duplicates,
        minutes_on_task=found.minutes_on_task,
        agent_runs=found.agents.runs,
        escalation_rate=found.agents.escalation_rate,
        success_rate=found.agents.success_rate,
        cost_sgd=found.agents.cost_sgd,
        budget_sgd=found.budget_sgd,
        target_cost_sgd=found.target_cost_sgd,
        cost_by_model=[CostLine(**row) for row in found.agents.by_model],
    )


@router.get("/kpis", response_model=KpisOut)
def portfolio(principal: CurrentPrincipal, session: DbSession) -> KpisOut:
    """Every bid this person can see, and the AI totals across them."""
    bids = list(session.execute(select(Bid).order_by(Bid.human_id)).scalars())
    rows = [kpis.bid_kpis(session, bid) for bid in bids]
    runs = sum(r.agents.runs for r in rows)
    escalated = sum(r.agents.escalated for r in rows)
    finished = sum(r.agents.finished for r in rows)
    succeeded = sum(r.agents.succeeded + r.agents.escalated for r in rows)
    return KpisOut(
        bids=[bid_out(r) for r in rows],
        agent_runs=runs,
        escalation_rate=escalated / finished if finished else None,
        success_rate=succeeded / finished if finished else None,
        cost_sgd=sum((r.agents.cost_sgd for r in rows), Decimal("0")),
        target_cost_sgd=kpis.target_cost_per_tender(),
        targets=dict(kpis.settings().get("targets") or {}),
    )


class BidPhase2Out(BaseModel):
    bid_id: uuid.UUID
    human_id: str
    received_on: date
    ready_on: date | None
    turnaround_working_days: int | None
    priced_lines: int
    sourced_lines: int
    price_provenance: float | None
    clarifications_issued: int
    clarifications_measured: int
    clarifications_minor: int
    clarification_acceptance: float | None


class Phase2Out(BaseModel):
    bids: list[BidPhase2Out]
    turnaround_working_days: float | None
    baseline_turnaround_working_days: float | None
    turnaround_reduction: float | None
    price_provenance: float | None
    clarification_acceptance: float | None
    targets: dict[str, Any]
    minor_edit_ratio: float


@router.get("/kpis/phase2", response_model=Phase2Out)
def phase2(principal: CurrentPrincipal, session: DbSession) -> Phase2Out:
    """The Phase 2 KPIs across the bids this person can see: tender turnaround in working
    days, price provenance, and clarifications issued with only minor edits."""
    bids = list(session.execute(select(Bid).order_by(Bid.human_id)).scalars())
    found = kpis.phase2_kpis(session, bids)
    return Phase2Out(
        bids=[
            BidPhase2Out(
                bid_id=row.bid_id,
                human_id=row.human_id,
                received_on=row.received_on,
                ready_on=row.ready_on,
                turnaround_working_days=row.turnaround_working_days,
                priced_lines=row.priced_lines,
                sourced_lines=row.sourced_lines,
                price_provenance=row.price_provenance,
                clarifications_issued=row.clarifications_issued,
                clarifications_measured=row.clarifications_measured,
                clarifications_minor=row.clarifications_minor,
                clarification_acceptance=row.clarification_acceptance,
            )
            for row in found.bids
        ],
        turnaround_working_days=found.turnaround_working_days,
        baseline_turnaround_working_days=found.baseline_turnaround_working_days,
        turnaround_reduction=found.turnaround_reduction,
        price_provenance=found.price_provenance,
        clarification_acceptance=found.clarification_acceptance,
        targets=found.targets,
        minor_edit_ratio=found.minor_edit_ratio,
    )


@router.get("/bids/{bid_id}/kpis", response_model=BidKpisOut)
def for_bid(context: CurrentBid, session: DbSession) -> BidKpisOut:
    """One bid's measures and its AI cost report by route, provider and model."""
    return bid_out(kpis.bid_kpis(session, context.bid))
