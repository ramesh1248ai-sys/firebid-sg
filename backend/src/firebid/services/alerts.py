"""Deadline alerts (FR-BID-03).

People are warned a configured number of days before a tender's submission deadline and its
clarification cut-off. Each deadline and interval notifies once: the sent record makes repeated
runs harmless.

Recipients are the people who own open work on that bid. When nobody does, the bid's managers
are told instead, so a deadline is never silently unowned.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from firebid.db.models.core import AppUser, Bid, BidMember, Organisation
from firebid.db.models.workflow import DeadlineAlert, HumanTask
from firebid.domain.state_machines import BidState, Role
from firebid.notifications import Notification, Notifier

# A bid in one of these states no longer needs deadline warnings.
CLOSED_STATES = {
    str(BidState.SUBMITTED),
    str(BidState.AWARDED),
    str(BidState.LOST),
    str(BidState.WITHDRAWN),
    str(BidState.NO_BID),
}
MANAGER_ROLES = {str(Role.BID_MANAGER), str(Role.SENIOR_ESTIMATOR)}
DEADLINES = (
    ("submission", "submission_deadline"),
    ("clarification_cutoff", "clarification_cutoff"),
)


@dataclass(frozen=True)
class DueAlert:
    bid: Bid
    deadline_kind: str
    deadline_at: datetime
    days_before: int
    recipients: list[AppUser]


def _recipients(session: Session, bid: Bid) -> list[AppUser]:
    owners = list(
        session.execute(
            select(AppUser)
            .join(HumanTask, HumanTask.assignee_id == AppUser.id)
            .where(HumanTask.bid_id == bid.id, HumanTask.state.in_(("open", "in_progress")))
            .distinct()
        )
        .scalars()
        .all()
    )
    if owners:
        return owners
    return list(
        session.execute(
            select(AppUser)
            .join(BidMember, BidMember.user_id == AppUser.id)
            .where(BidMember.bid_id == bid.id, BidMember.role.in_(MANAGER_ROLES))
            .distinct()
        )
        .scalars()
        .all()
    )


def due_alerts(session: Session, now: datetime | None = None) -> list[DueAlert]:
    """Deadlines that have come within a configured interval and have not been notified yet."""
    now = now or datetime.now(UTC)
    intervals_by_org = {
        organisation.id: sorted(organisation.deadline_alert_days, reverse=True)
        for organisation in session.execute(select(Organisation)).scalars().all()
    }
    already_sent = {
        (alert.bid_id, alert.deadline_kind, alert.days_before)
        for alert in session.execute(select(DeadlineAlert)).scalars().all()
    }

    due: list[DueAlert] = []
    open_bids = session.execute(select(Bid).where(Bid.state.not_in(CLOSED_STATES))).scalars().all()
    for bid in open_bids:
        intervals = intervals_by_org.get(bid.organisation_id, [7, 3, 1])
        for kind, attribute in DEADLINES:
            deadline: datetime | None = getattr(bid, attribute)
            if deadline is None or deadline < now:
                continue
            remaining_days = (deadline - now).total_seconds() / 86_400
            reached = [days for days in intervals if remaining_days <= days]
            if not reached:
                continue
            # The tightest interval reached that has not been sent yet.
            for days in sorted(reached):
                if (bid.id, kind, days) in already_sent:
                    break
                due.append(
                    DueAlert(
                        bid=bid,
                        deadline_kind=kind,
                        deadline_at=deadline,
                        days_before=days,
                        recipients=_recipients(session, bid),
                    )
                )
                break
    return due


def _message(alert: DueAlert) -> tuple[str, str]:
    what = "submission deadline" if alert.deadline_kind == "submission" else "clarification cut-off"
    when = alert.deadline_at.strftime("%d %b %Y %H:%M")
    subject = f"{alert.bid.human_id}: {what} in {alert.days_before} day(s)"
    body = (
        f"{alert.bid.human_id} for {alert.bid.client_name} ({alert.bid.tender_reference})\n"
        f"The {what} is {when}.\n"
        f"Open the bid in FireBid SG to see what is outstanding."
    )
    return subject, body


def send_deadline_alerts(
    session: Session, notifier: Notifier, now: datetime | None = None
) -> list[DueAlert]:
    """Send what is due and record it. Returns the alerts sent."""
    sent: list[DueAlert] = []
    for alert in due_alerts(session, now):
        subject, body = _message(alert)
        for person in alert.recipients:
            notifier.send(
                Notification(
                    recipient_email=person.username,
                    subject=subject,
                    body=body,
                    kind=f"deadline.{alert.deadline_kind}",
                    bid_id=alert.bid.id,
                )
            )
        session.add(
            DeadlineAlert(
                bid_id=alert.bid.id,
                deadline_kind=alert.deadline_kind,
                days_before=alert.days_before,
                recipient_count=len(alert.recipients),
            )
        )
        try:
            session.flush()
        except IntegrityError:
            # Another worker sent this one first; leave its record in place.
            session.rollback()
            continue
        sent.append(alert)
    return sent


def alert_recipients(session: Session, bid_id: uuid.UUID) -> list[AppUser]:
    """Who would be told about this bid's deadlines right now."""
    bid = session.get(Bid, bid_id)
    return _recipients(session, bid) if bid else []
