"""Audit trail API (FR-ADM-04): filtered, paginated reads and a CSV export.

Access control arrives with P0-03, which scopes every bid-owned query to its members.
"""

from __future__ import annotations

import csv
import io
import uuid
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import Select, select, tuple_
from sqlalchemy.orm import Session

from firebid.api.deps import get_session
from firebid.db.audit import verify_chain
from firebid.db.models.audit import AuditEvent

router = APIRouter(prefix="/audit", tags=["audit"])

MAX_PAGE = 200
EXPORT_LIMIT = 100_000
CSV_COLUMNS = [
    "occurred_at",
    "action",
    "entity_type",
    "entity_id",
    "actor_label",
    "bid_id",
    "reason",
    "before",
    "after",
]


class AuditEventOut(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    bid_id: uuid.UUID | None
    actor_id: uuid.UUID | None
    actor_label: str
    action: str
    entity_type: str
    entity_id: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    reason: str | None

    model_config = {"from_attributes": True}


class AuditPage(BaseModel):
    items: list[AuditEventOut]
    next_cursor: str | None


class ChainStatus(BaseModel):
    chain_key: uuid.UUID
    intact: bool
    problems: list[str]


class AuditFilters:
    """The filters FR-ADM-04 asks for: bid, entity, actor, action and time range."""

    def __init__(
        self,
        bid_id: Annotated[uuid.UUID | None, Query()] = None,
        entity_type: Annotated[str | None, Query(max_length=80)] = None,
        entity_id: Annotated[str | None, Query(max_length=80)] = None,
        actor_id: Annotated[uuid.UUID | None, Query()] = None,
        action: Annotated[str | None, Query(max_length=120)] = None,
        occurred_from: Annotated[datetime | None, Query()] = None,
        occurred_to: Annotated[datetime | None, Query()] = None,
    ) -> None:
        self.bid_id = bid_id
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.actor_id = actor_id
        self.action = action
        self.occurred_from = occurred_from
        self.occurred_to = occurred_to

    def apply(self, statement: Select[tuple[AuditEvent]]) -> Select[tuple[AuditEvent]]:
        if self.bid_id:
            statement = statement.where(AuditEvent.bid_id == self.bid_id)
        if self.entity_type:
            statement = statement.where(AuditEvent.entity_type == self.entity_type)
        if self.entity_id:
            statement = statement.where(AuditEvent.entity_id == self.entity_id)
        if self.actor_id:
            statement = statement.where(AuditEvent.actor_id == self.actor_id)
        if self.action:
            statement = statement.where(AuditEvent.action.ilike(f"%{self.action}%"))
        if self.occurred_from:
            statement = statement.where(AuditEvent.occurred_at >= self.occurred_from)
        if self.occurred_to:
            statement = statement.where(AuditEvent.occurred_at <= self.occurred_to)
        return statement


def encode_cursor(record: AuditEvent) -> str:
    return urlsafe_b64encode(f"{record.occurred_at.isoformat()}|{record.id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        occurred_at, identifier = urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        return datetime.fromisoformat(occurred_at), uuid.UUID(identifier)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc


@router.get("", response_model=AuditPage)
def list_events(
    session: Annotated[Session, Depends(get_session)],
    filters: Annotated[AuditFilters, Depends()],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> AuditPage:
    """Newest first. Paging is by keyset, so results stay stable as new events arrive."""
    statement = filters.apply(select(AuditEvent)).order_by(
        AuditEvent.occurred_at.desc(), AuditEvent.id.desc()
    )
    if cursor:
        occurred_at, identifier = decode_cursor(cursor)
        statement = statement.where(
            tuple_(AuditEvent.occurred_at, AuditEvent.id) < (occurred_at, identifier)
        )
    records = list(session.execute(statement.limit(limit + 1)).scalars().all())
    has_more = len(records) > limit
    page = records[:limit]
    return AuditPage(
        items=[AuditEventOut.model_validate(record) for record in page],
        next_cursor=encode_cursor(page[-1]) if has_more and page else None,
    )


@router.get("/export.csv")
def export_csv(
    session: Annotated[Session, Depends(get_session)],
    filters: Annotated[AuditFilters, Depends()],
) -> StreamingResponse:
    """The same filters, streamed as CSV so a large range does not build up in memory."""
    statement = (
        filters.apply(select(AuditEvent))
        .order_by(AuditEvent.occurred_at, AuditEvent.id)
        .limit(EXPORT_LIMIT)
    )

    def rows() -> Iterator[str]:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(CSV_COLUMNS)
        yield buffer.getvalue()
        for record in session.execute(statement).scalars().yield_per(500):
            buffer.seek(0)
            buffer.truncate(0)
            writer.writerow(
                [
                    record.occurred_at.isoformat(),
                    record.action,
                    record.entity_type,
                    record.entity_id,
                    record.actor_label,
                    record.bid_id or "",
                    record.reason or "",
                    record.before or "",
                    record.after or "",
                ]
            )
            yield buffer.getvalue()

    return StreamingResponse(
        rows(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit-events.csv"'},
    )


@router.get("/chain/{chain_key}", response_model=ChainStatus)
def chain_status(
    chain_key: uuid.UUID, session: Annotated[Session, Depends(get_session)]
) -> ChainStatus:
    """Recompute a chain from its events: does the recorded history still add up?"""
    problems = verify_chain(session, chain_key)
    return ChainStatus(
        chain_key=chain_key,
        intact=not problems,
        problems=[f"link {problem.seq}: {problem.detail}" for problem in problems],
    )
