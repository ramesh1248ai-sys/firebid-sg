"""Addenda: register one, upload its files, and ask what it changed (FR-DOC-05)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from firebid.api.deps import CurrentBid, DbSession, require
from firebid.auth.permissions import Action
from firebid.auth.provisioning import Principal
from firebid.db.models.documents import Addendum
from firebid.services.addenda import AddendumExists, affected_items, register_addendum

router = APIRouter(prefix="/bids/{bid_id}/addenda", tags=["addenda"])


class AddendumIn(BaseModel):
    number: str = Field(min_length=1, max_length=40)
    issued_on: date | None = None
    summary: str | None = Field(default=None, max_length=4000)


class AddendumOut(BaseModel):
    id: uuid.UUID
    number: str
    issued_on: date | None
    summary: str | None

    model_config = {"from_attributes": True}


class AffectedItemOut(BaseModel):
    kind: str
    id: uuid.UUID
    reference: str
    revision: str | None
    state: str
    replaces: str | None
    detail: dict[str, Any]

    model_config = {"from_attributes": True}


@router.post("", response_model=AddendumOut, status_code=status.HTTP_201_CREATED)
def create(
    body: AddendumIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.DOCUMENT_UPLOAD)],
) -> AddendumOut:
    """Register an addendum; its files are then uploaded with its id."""
    try:
        addendum = register_addendum(
            session,
            context.bid.id,
            number=body.number,
            issued_on=body.issued_on,
            summary=body.summary,
            actor=principal.actor(),
        )
    except AddendumExists as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal
    return AddendumOut.model_validate(addendum)


@router.get("", response_model=list[AddendumOut])
def list_addenda(context: CurrentBid, session: DbSession) -> list[AddendumOut]:
    rows = session.execute(
        select(Addendum)
        .where(Addendum.bid_id == context.bid.id)
        .order_by(Addendum.issued_on.nulls_last(), Addendum.number)
    ).scalars()
    return [AddendumOut.model_validate(addendum) for addendum in rows]


@router.get("/{addendum_id}/affected", response_model=list[AffectedItemOut])
def affected(
    addendum_id: uuid.UUID, context: CurrentBid, session: DbSession
) -> list[AffectedItemOut]:
    """Everything this addendum changed: the new revisions, and what each one replaced."""
    addendum = session.get(Addendum, addendum_id)
    if addendum is None or addendum.bid_id != context.bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "addendum not found")
    return [AffectedItemOut.model_validate(item) for item in affected_items(session, addendum)]
