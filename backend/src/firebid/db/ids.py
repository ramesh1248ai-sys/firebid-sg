"""Human-readable IDs (BID-2026-014, QTO-000347), handed out one at a time under concurrency."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

_NEXT_VALUE = text(
    """
    INSERT INTO id_counter (scope, name, value) VALUES (:scope, :name, 1)
    ON CONFLICT (scope, name) DO UPDATE SET value = id_counter.value + 1
    RETURNING value
    """
)


def next_counter(session: Session, scope: str, name: str) -> int:
    """Atomic increment: concurrent callers each get their own number."""
    return int(session.execute(_NEXT_VALUE, {"scope": scope, "name": name}).scalar_one())


def next_bid_id(session: Session, organisation_id: uuid.UUID, year: int | None = None) -> str:
    year = year or datetime.now(UTC).year
    value = next_counter(session, "bid", f"{organisation_id}:{year}")
    return f"BID-{year}-{value:03d}"


def next_qto_id(session: Session, bid_id: uuid.UUID) -> str:
    return f"QTO-{next_counter(session, 'qto_item', str(bid_id)):06d}"
