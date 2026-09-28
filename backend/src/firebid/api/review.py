"""The verification workbench's queue and actions (FR-REV-02, FR-REV-03, FR-REV-04).

The queue lists the takeoff riskiest first: (1 - calibrated confidence) x impact. Actions
work on one item or many, and each returns the recorded action, which is what undo takes.
Edit and reject need a reason code.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from firebid.api.deps import CurrentBid, DbSession, require
from firebid.api.qto import ItemOut, MeasureIn, _measured, _missing, _people, item_out
from firebid.auth.permissions import Action
from firebid.auth.provisioning import Principal
from firebid.db.models.review import ReviewAction
from firebid.domain.state_machines import TransitionError
from firebid.services import qto, review
from firebid.services import review_actions as actions

router = APIRouter(prefix="/bids/{bid_id}/review", tags=["review"])


class QueueRowOut(BaseModel):
    item: ItemOut
    risk: float
    impact: float
    system: str
    sheet_ids: list[str]


class ReasonOut(BaseModel):
    code: str
    label: str


class ItemsIn(BaseModel):
    item_ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=5000)]
    note: str | None = None


class RejectIn(ItemsIn):
    reason_code: str


class EditIn(BaseModel):
    reason_code: str
    note: str | None = None
    attributes: dict[str, str] | None = None
    quantity: Annotated[Decimal, Field(ge=0)] | None = None
    measure: MeasureIn | None = None
    allowance_percent: Annotated[Decimal, Field(ge=0, le=100)] | None = None


class DetectionsIn(BaseModel):
    detection_ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=5000)]
    reason_code: str
    note: str | None = None


class ActionOut(BaseModel):
    id: uuid.UUID
    kind: str
    actor: str
    reason_code: str | None
    note: str | None
    item_ids: list[str]
    detection_ids: list[str]
    count: int
    created_at: datetime
    undone: bool
    undoes_id: uuid.UUID | None


class CoverageOut(BaseModel):
    items_total: int
    items_verified: int
    items_percent: float
    value_total: float
    value_verified: float
    value_percent: float
    value_basis: str
    policy_percent: float
    met: bool


def action_out(row: ReviewAction) -> ActionOut:
    entries: list[dict[str, Any]] = [dict(e) for e in row.entries]
    return ActionOut(
        id=row.id,
        kind=row.kind,
        actor=row.actor_label,
        reason_code=row.reason_code,
        note=row.note,
        item_ids=[str(e["item_id"]) for e in entries if "item_id" in e],
        detection_ids=[str(e["detection_id"]) for e in entries if "detection_id" in e],
        count=len(entries),
        created_at=row.created_at,
        undone=row.undone_at is not None,
        undoes_id=row.undoes_id,
    )


def _refused(error: Exception) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(error))


@router.get("/queue", response_model=list[QueueRowOut])
def queue(
    context: CurrentBid,
    session: DbSession,
    sheet_id: Annotated[str | None, Query()] = None,
    system: Annotated[str | None, Query()] = None,
    level: Annotated[str | None, Query()] = None,
    item_type: Annotated[str | None, Query()] = None,
    status_: Annotated[str | None, Query(alias="status")] = None,
) -> list[QueueRowOut]:
    """Riskiest first; items still to decide before decided ones (FR-REV-02)."""
    rows = review.queue(
        session,
        context.bid.id,
        sheet_id=sheet_id,
        system=system,
        level=level,
        item_type=item_type,
        status=status_,
    )
    missing = _missing(session, context.bid.id)
    people = _people(session, [r.item for r in rows])
    return [
        QueueRowOut(
            item=item_out(r.item, missing.get(r.item.id, []), people),
            risk=round(r.risk, 4),
            impact=round(r.impact, 4),
            system=r.system,
            sheet_ids=r.sheet_ids,
        )
        for r in rows
    ]


@router.get("/reasons", response_model=list[ReasonOut])
def reasons(context: CurrentBid) -> list[ReasonOut]:
    return [ReasonOut(code=k, label=v) for k, v in review.reason_codes().items()]


@router.get("/coverage", response_model=CoverageOut)
def coverage(context: CurrentBid, session: DbSession) -> CoverageOut:
    """Verified share of the takeoff, by items and by value (FR-REV-04)."""
    return CoverageOut(**review.coverage(session, context.bid.id))


@router.get("/actions", response_model=list[ActionOut])
def recent_actions(
    context: CurrentBid, session: DbSession, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[ActionOut]:
    """The bid's latest actions, newest first: what undo offers."""
    rows = session.execute(
        select(ReviewAction)
        .where(ReviewAction.bid_id == context.bid.id)
        .order_by(ReviewAction.created_at.desc())
        .limit(limit)
    ).scalars()
    return [action_out(row) for row in rows]


@router.post("/accept", response_model=ActionOut)
def accept(
    body: ItemsIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.QTO_EDIT)],
) -> ActionOut:
    try:
        row = actions.accept(session, context.bid.id, body.item_ids, principal.actor(), body.note)
    except (actions.ReviewError, TransitionError) as refusal:
        raise _refused(refusal) from refusal
    return action_out(row)


@router.post("/reject", response_model=ActionOut)
def reject(
    body: RejectIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.QTO_EDIT)],
) -> ActionOut:
    try:
        row = actions.reject(
            session, context.bid.id, body.item_ids, principal.actor(), body.reason_code, body.note
        )
    except (actions.ReviewError, TransitionError) as refusal:
        raise _refused(refusal) from refusal
    return action_out(row)


@router.post("/items/{item_id}/edit", response_model=ActionOut)
def edit(
    item_id: uuid.UUID,
    body: EditIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.QTO_EDIT)],
) -> ActionOut:
    try:
        row = actions.edit(
            session,
            context.bid.id,
            item_id,
            principal.actor(),
            body.reason_code,
            body.note,
            attributes=body.attributes,
            quantity=body.quantity,
            measured=_measured(session, context, body.measure),
            allowance_percent=body.allowance_percent,
        )
    except (actions.ReviewError, qto.QtoError, TransitionError) as refusal:
        raise _refused(refusal) from refusal
    return action_out(row)


@router.post("/detections/reject", response_model=ActionOut)
def reject_detections(
    body: DetectionsIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.QTO_EDIT)],
) -> ActionOut:
    """What was found is not there: the detections leave takeoff, which is recomputed."""
    try:
        row = actions.reject_detections(
            session,
            context.bid.id,
            body.detection_ids,
            principal.actor(),
            body.reason_code,
            body.note,
        )
    except (actions.ReviewError, TransitionError) as refusal:
        raise _refused(refusal) from refusal
    return action_out(row)


@router.post("/actions/{action_id}/undo", response_model=ActionOut)
def undo(
    action_id: uuid.UUID,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.QTO_EDIT)],
) -> ActionOut:
    try:
        row = actions.undo(session, context.bid.id, action_id, principal.actor())
    except (actions.ReviewError, TransitionError) as refusal:
        raise _refused(refusal) from refusal
    return action_out(row)
