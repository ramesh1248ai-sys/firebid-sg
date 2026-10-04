"""The Phase 1 KPIs a live bid can show (requirements §14, NFR-14, NFR-15; P1-11).

Accuracy against verified truth (sprinkler counts, pipe length) needs a golden set and comes
from `firebid-eval`. What a live bid measures on its own:

* **False-detection rate:** AI-proposed items a person rejected ÷ AI-proposed items.
* **Missed-item rate:** items a person added by hand ÷ items in the verified takeoff. Only
  what the reviewer noticed: a lower bound on what the platform missed.
* **Duplicates:** groups found, and how many are still unresolved (zero at G1).
* **QTO effort:** workbench time on task (`services.effort`).
* **Escalation rate** and **workflow and tool success:** from agent runs.
* **AI cost:** by route, provider and model, against the bid's budget and the target cost per
  tender.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from firebid.db.models.ai import BidBudget
from firebid.db.models.core import Bid
from firebid.db.models.takeoff import DuplicateGroup, QtoItem
from firebid.db.models.workflow import AgentRun
from firebid.services import effort

CONFIG = Path(__file__).resolve().parents[3] / "config" / "kpi.yaml"
LIVE = ("proposed", "edited", "verified", "baselined", "rejected")


@cache
def settings() -> dict[str, Any]:
    return dict(yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {})


def target_cost_per_tender() -> Decimal | None:
    value = settings().get("target_cost_per_tender_sgd")
    return Decimal(str(value)) if value is not None else None


@dataclass
class AgentStats:
    runs: int = 0
    succeeded: int = 0
    failed: int = 0
    escalated: int = 0
    cost_sgd: Decimal = Decimal("0")
    by_model: list[dict[str, Any]] = field(default_factory=list)

    @property
    def finished(self) -> int:
        return self.succeeded + self.failed + self.escalated

    @property
    def escalation_rate(self) -> float | None:
        return self.escalated / self.finished if self.finished else None

    @property
    def success_rate(self) -> float | None:
        """Runs that completed without failure. An escalation is not a failure: it is the
        runtime handing the work to a person, as designed."""
        return (self.succeeded + self.escalated) / self.finished if self.finished else None


def agent_stats(session: Session, bid_id: uuid.UUID | None = None) -> AgentStats:
    query = select(
        AgentRun.route,
        AgentRun.provider,
        AgentRun.model,
        AgentRun.state,
        func.count(),
        func.coalesce(func.sum(AgentRun.cost_sgd), 0),
    ).group_by(AgentRun.route, AgentRun.provider, AgentRun.model, AgentRun.state)
    if bid_id is not None:
        query = query.where(AgentRun.bid_id == bid_id)
    out = AgentStats()
    grouped: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"runs": 0, "cost_sgd": Decimal("0")}
    )
    for route, provider, model, state, count, cost in session.execute(query).tuples():
        out.runs += count
        if state == "succeeded":
            out.succeeded += count
        elif state == "failed":
            out.failed += count
        elif state == "escalated":
            out.escalated += count
        out.cost_sgd += Decimal(cost or 0)
        row = grouped[(route, provider or "-", model or "-")]
        row["runs"] += count
        row["cost_sgd"] += Decimal(cost or 0)
    out.by_model = [
        {"route": r, "provider": p, "model": m, **values}
        for (r, p, m), values in sorted(grouped.items(), key=lambda kv: -kv[1]["cost_sgd"])
    ]
    return out


@dataclass
class BidKpis:
    bid_id: uuid.UUID
    human_id: str
    ai_items: int
    rejected_ai_items: int
    manual_items: int
    verified_items: int
    duplicate_groups: int
    unresolved_duplicates: int
    minutes_on_task: int
    agents: AgentStats
    budget_sgd: Decimal | None
    target_cost_sgd: Decimal | None

    @property
    def false_detection_rate(self) -> float | None:
        return self.rejected_ai_items / self.ai_items if self.ai_items else None

    @property
    def missed_item_rate(self) -> float | None:
        return self.manual_items / self.verified_items if self.verified_items else None


def bid_kpis(session: Session, bid: Bid) -> BidKpis:
    counts: dict[tuple[bool, str], int] = {
        (is_manual, state): n
        for is_manual, state, n in session.execute(
            select(QtoItem.is_manual, QtoItem.state, func.count())
            .where(QtoItem.bid_id == bid.id, QtoItem.state.in_(LIVE))
            .group_by(QtoItem.is_manual, QtoItem.state)
        ).tuples()
    }
    ai_items = sum(n for (manual, _), n in counts.items() if not manual)
    rejected = sum(n for (manual, state), n in counts.items() if not manual and state == "rejected")
    manual = sum(n for (is_manual, state), n in counts.items() if is_manual and state != "rejected")
    verified = sum(n for (_, state), n in counts.items() if state in ("verified", "baselined"))
    groups = dict(
        session.execute(
            select(DuplicateGroup.status, func.count())
            .where(DuplicateGroup.bid_id == bid.id)
            .group_by(DuplicateGroup.status)
        )
        .tuples()
        .all()
    )
    budget = session.execute(
        select(BidBudget.limit_sgd).where(BidBudget.bid_id == bid.id)
    ).scalar_one_or_none()
    return BidKpis(
        bid_id=bid.id,
        human_id=bid.human_id,
        ai_items=ai_items,
        rejected_ai_items=rejected,
        manual_items=manual,
        verified_items=verified,
        duplicate_groups=sum(groups.values()),
        unresolved_duplicates=groups.get("unresolved", 0),
        minutes_on_task=effort.time_on_task(session, bid.id).minutes,
        agents=agent_stats(session, bid.id),
        budget_sgd=budget,
        target_cost_sgd=target_cost_per_tender(),
    )


# --- Phase 2 (requirements §14; P2-09) -------------------------------------------------------


def phase2_settings() -> dict[str, Any]:
    return dict(settings().get("phase2") or {})


def minor_edit_threshold() -> float:
    return float(phase2_settings().get("clarification_minor_edit_ratio", 0.2))


def baseline_turnaround() -> float | None:
    value = phase2_settings().get("baseline_turnaround_working_days")
    return float(value) if value is not None else None


@dataclass
class BidPhase2:
    bid_id: uuid.UUID
    human_id: str
    received_on: date
    # The day G3 approved the estimate: ready to submit.
    ready_on: date | None
    turnaround_working_days: int | None
    priced_lines: int
    sourced_lines: int
    clarifications_issued: int
    clarifications_measured: int
    clarifications_minor: int

    @property
    def price_provenance(self) -> float | None:
        return self.sourced_lines / self.priced_lines if self.priced_lines else None

    @property
    def clarification_acceptance(self) -> float | None:
        if not self.clarifications_measured:
            return None
        return self.clarifications_minor / self.clarifications_measured


def bid_phase2(session: Session, bid: Bid) -> BidPhase2:
    """A bid's Phase 2 measures: turnaround from its lifecycle, price provenance from its
    current bill, clarification acceptance from what was drafted and what was issued."""
    from firebid.db.models.clarifications import Clarification
    from firebid.db.models.workflow import Approval
    from firebid.kpi import phase2
    from firebid.services import boq

    holidays = frozenset(
        value if isinstance(value, date) else date.fromisoformat(str(value))
        for value in phase2_settings().get("holidays") or []
    )
    ready = session.execute(
        select(func.min(Approval.decided_at)).where(
            Approval.bid_id == bid.id, Approval.gate == "G3", Approval.decision == "approved"
        )
    ).scalar_one_or_none()
    received = bid.created_at.date()
    current = boq.current_boq(session, bid.id)
    lines = boq.lines_of(session, current) if current is not None else []
    share = phase2.provenance(
        [
            (
                line.amount is not None,
                line.rate_id is not None or bool((line.allowance_by or "").strip()),
            )
            for line in lines
        ]
    )
    issued = [
        row
        for row in session.execute(
            select(Clarification).where(
                Clarification.bid_id == bid.id, Clarification.issued_at.is_not(None)
            )
        ).scalars()
    ]
    measured = [row for row in issued if "minor_edits" in (row.drafting or {})]
    return BidPhase2(
        bid_id=bid.id,
        human_id=bid.human_id,
        received_on=received,
        ready_on=ready.date() if ready else None,
        turnaround_working_days=phase2.working_days(received, ready.date(), holidays)
        if ready
        else None,
        priced_lines=share.of,
        sourced_lines=share.count,
        clarifications_issued=len(issued),
        clarifications_measured=len(measured),
        clarifications_minor=sum(1 for row in measured if row.drafting.get("minor_edits")),
    )


@dataclass
class Phase2Kpis:
    bids: list[BidPhase2]
    turnaround_working_days: float | None
    baseline_turnaround_working_days: float | None
    turnaround_reduction: float | None
    price_provenance: float | None
    clarification_acceptance: float | None
    targets: dict[str, Any]
    minor_edit_ratio: float


def phase2_kpis(session: Session, bids: list[Bid]) -> Phase2Kpis:
    """The Phase 2 KPIs across the bids given. A measure with nothing to measure is None,
    not zero: an exit report says "pending", not "missed"."""
    from firebid.kpi import phase2

    rows = [bid_phase2(session, bid) for bid in bids]
    turned = [
        row.turnaround_working_days for row in rows if row.turnaround_working_days is not None
    ]
    mean = sum(turned) / len(turned) if turned else None
    priced = sum(row.priced_lines for row in rows)
    measured = sum(row.clarifications_measured for row in rows)
    baseline = baseline_turnaround()
    return Phase2Kpis(
        bids=rows,
        turnaround_working_days=mean,
        baseline_turnaround_working_days=baseline,
        turnaround_reduction=phase2.reduction(baseline, mean),
        price_provenance=sum(row.sourced_lines for row in rows) / priced if priced else None,
        clarification_acceptance=sum(row.clarifications_minor for row in rows) / measured
        if measured
        else None,
        targets=dict(phase2_settings().get("targets") or {}),
        minor_edit_ratio=minor_edit_threshold(),
    )
