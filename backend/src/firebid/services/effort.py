"""Time on task: how long people spent taking a bid off in the workbench (P1-11).

The workbench sends a heartbeat each minute a person is working in it (the page is visible
and they have used it in that minute). Each heartbeat records one row for its bid, person,
minute and area; several in one minute are one. Time on task is the count of those minutes.
It is the AI-assisted side of the QTO effort KPI (requirements §14), set against an
estimator's manual takeoff hours in shadow mode.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from firebid.db.models.review import ACTIVITY_AREAS, ActivityMinute


def record(
    session: Session,
    bid_id: uuid.UUID,
    user_id: uuid.UUID,
    area: str = "review",
    at: datetime | None = None,
) -> None:
    """A heartbeat: this person was working on this bid in this minute."""
    if area not in ACTIVITY_AREAS:
        raise ValueError(f"unknown workbench area {area!r}")
    minute = (at or datetime.now(UTC)).replace(second=0, microsecond=0)
    session.execute(
        insert(ActivityMinute)
        .values(id=uuid.uuid4(), bid_id=bid_id, user_id=user_id, minute=minute, area=area)
        .on_conflict_do_nothing(constraint="uq_activity_minute")
    )


@dataclass
class Effort:
    minutes: int = 0
    by_area: dict[str, int] = field(default_factory=dict)
    people: int = 0

    @property
    def hours(self) -> float:
        return round(self.minutes / 60, 2)


def time_on_task(session: Session, bid_id: uuid.UUID) -> Effort:
    """Minutes worked on a bid's takeoff, by area and in all. A minute in two areas counts
    once in the total."""
    by_area = dict(
        session.execute(
            select(ActivityMinute.area, func.count())
            .where(ActivityMinute.bid_id == bid_id)
            .group_by(ActivityMinute.area)
        )
        .tuples()
        .all()
    )
    total = session.execute(
        select(func.count()).select_from(
            select(ActivityMinute.user_id, ActivityMinute.minute)
            .where(ActivityMinute.bid_id == bid_id)
            .distinct()
            .subquery()
        )
    ).scalar_one()
    people = session.execute(
        select(func.count(func.distinct(ActivityMinute.user_id))).where(
            ActivityMinute.bid_id == bid_id
        )
    ).scalar_one()
    return Effort(
        minutes=int(total), by_area={k: int(v) for k, v in by_area.items()}, people=int(people)
    )
