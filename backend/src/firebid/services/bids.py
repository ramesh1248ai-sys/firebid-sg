"""Bid workspace rules: what a bid needs before work starts, and how it is created."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from firebid.auth.provisioning import Principal
from firebid.db.audit import record_event
from firebid.db.ids import next_bid_id
from firebid.db.models.core import Bid, BidMember, Project
from firebid.db.models.workflow import Approval, HumanTask
from firebid.domain.actors import AuditContext
from firebid.domain.state_machines import BidState

# FR-BID-01: a bid stays in Registered until these are known.
MANDATORY_FIELDS = (
    "client_name",
    "tender_reference",
    "submission_deadline",
    "clarification_cutoff",
    "tender_validity_days",
)


def missing_mandatory_fields(session: Session, bid: Bid) -> list[str]:
    """Which of the required details are still missing, including the bid team."""
    missing = [field for field in MANDATORY_FIELDS if getattr(bid, field) in (None, "")]
    members = session.execute(
        select(func.count()).select_from(BidMember).where(BidMember.bid_id == bid.id)
    ).scalar_one()
    if not members:
        missing.append("bid_team")
    return missing


@dataclass(frozen=True)
class NewBid:
    project_name: str
    client_name: str
    tender_reference: str
    submission_deadline: datetime
    clarification_cutoff: datetime | None = None
    tender_validity_days: int | None = None
    project_id: uuid.UUID | None = None


def create_bid(session: Session, principal: Principal, details: NewBid) -> Bid:
    """Create a bid in Registered, with its creator as the first member."""
    if details.project_id is not None:
        project = session.get(Project, details.project_id)
        if project is None:
            raise ValueError("project not found")
    else:
        project = Project(
            organisation_id=principal.organisation_id,
            name=details.project_name,
            created_by_id=principal.user_id,
        )
        session.add(project)
        session.flush()

    bid = Bid(
        organisation_id=principal.organisation_id,
        project_id=project.id,
        human_id=next_bid_id(session, principal.organisation_id),
        client_name=details.client_name,
        tender_reference=details.tender_reference,
        submission_deadline=details.submission_deadline,
        clarification_cutoff=details.clarification_cutoff,
        tender_validity_days=details.tender_validity_days,
        state=str(BidState.REGISTERED),
        created_by_id=principal.user_id,
    )
    session.add(bid)
    session.flush()

    for role in sorted(principal.roles):
        session.add(BidMember(bid_id=bid.id, user_id=principal.user_id, role=role))
        break  # the creator joins under one role; more members are added explicitly

    record_event(
        session,
        context=AuditContext(organisation_id=bid.organisation_id, bid_id=bid.id),
        actor=principal.actor(),
        action="bid: created",
        entity_type="bid",
        entity_id=bid.id,
        after={"human_id": bid.human_id, "client_name": bid.client_name},
    )
    session.flush()
    return bid


@dataclass(frozen=True)
class BidSummary:
    """One row of the dashboard."""

    bid: Bid
    open_tasks: int
    overdue_tasks: int
    gates_passed: list[str]
    days_to_submission: int | None
    days_to_clarification_cutoff: int | None


def _days_until(moment: datetime | None, now: datetime) -> int | None:
    return None if moment is None else (moment - now).days


def dashboard(
    session: Session, principal: Principal, now: datetime | None = None
) -> list[BidSummary]:
    """Active bids the caller belongs to, with deadlines, gates and task counts."""
    now = now or datetime.now(UTC)
    bids = list(
        session.execute(
            select(Bid)
            .join(BidMember, BidMember.bid_id == Bid.id)
            .where(BidMember.user_id == principal.user_id)
            .order_by(Bid.submission_deadline)
        )
        .scalars()
        .all()
    )

    summaries = []
    for bid in bids:
        open_tasks = session.execute(
            select(func.count())
            .select_from(HumanTask)
            .where(HumanTask.bid_id == bid.id, HumanTask.state.in_(("open", "in_progress")))
        ).scalar_one()
        overdue = session.execute(
            select(func.count())
            .select_from(HumanTask)
            .where(
                HumanTask.bid_id == bid.id,
                HumanTask.state.in_(("open", "in_progress")),
                HumanTask.due_at < now,
            )
        ).scalar_one()
        gates = list(
            session.execute(
                select(Approval.gate)
                .where(Approval.bid_id == bid.id, Approval.decision == "approved")
                .order_by(Approval.gate)
            )
            .scalars()
            .all()
        )
        summaries.append(
            BidSummary(
                bid=bid,
                open_tasks=open_tasks,
                overdue_tasks=overdue,
                gates_passed=gates,
                days_to_submission=_days_until(bid.submission_deadline, now),
                days_to_clarification_cutoff=_days_until(bid.clarification_cutoff, now),
            )
        )
    return summaries
