"""The deployment guard (NFR-03): no planned maintenance within 48 hours of any active bid's
submission deadline.

A release or maintenance window is checked before it starts. It is refused when any bid
still being prepared (registered, qualifying, in preparation, under review, or approved
for submission) has its submission deadline within 48 hours of the window, before or
after. Estimators must never lose the platform in the last two days before a submission.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.models.core import Bid
from firebid.domain.state_machines import BidState

QUIET_HOURS = 48
ACTIVE = (
    BidState.REGISTERED,
    BidState.QUALIFYING,
    BidState.IN_PREPARATION,
    BidState.UNDER_REVIEW,
    BidState.APPROVED_FOR_SUBMISSION,
)


@dataclass(frozen=True)
class Conflict:
    human_id: str
    tender_reference: str
    submission_deadline: datetime
    hours_from_window: float


@dataclass(frozen=True)
class Verdict:
    window_start: datetime
    window_end: datetime
    conflicts: tuple[Conflict, ...]

    @property
    def allowed(self) -> bool:
        return not self.conflicts

    def summary(self) -> str:
        if self.allowed:
            return (
                f"window {self.window_start:%d %b %H:%M}-{self.window_end:%H:%M} UTC is clear: "
                f"no active bid's submission deadline is within {QUIET_HOURS} h"
            )
        listed = ", ".join(
            f"{c.human_id} due {c.submission_deadline:%d %b %H:%M} UTC" for c in self.conflicts[:5]
        )
        if len(self.conflicts) > 5:
            listed += f" and {len(self.conflicts) - 5} more"
        return f"window refused: {len(self.conflicts)} bid(s) due within {QUIET_HOURS} h ({listed})"


def check(
    session: Session, window_start: datetime, window_end: datetime, hours: int = QUIET_HOURS
) -> Verdict:
    """Whether planned maintenance may run from `window_start` to `window_end`."""
    if window_end < window_start:
        raise ValueError("the window ends before it starts")
    quiet = timedelta(hours=hours)
    rows = session.execute(
        select(Bid)
        .where(
            Bid.state.in_([str(state) for state in ACTIVE]),
            Bid.submission_deadline >= window_start - quiet,
            Bid.submission_deadline <= window_end + quiet,
        )
        .order_by(Bid.submission_deadline)
    ).scalars()
    conflicts = []
    for bid in rows:
        deadline = bid.submission_deadline
        if deadline < window_start:
            gap = (window_start - deadline).total_seconds() / 3600
        elif deadline > window_end:
            gap = (deadline - window_end).total_seconds() / 3600
        else:
            gap = 0.0
        conflicts.append(Conflict(bid.human_id, bid.tender_reference, deadline, round(gap, 1)))
    return Verdict(window_start, window_end, tuple(conflicts))
