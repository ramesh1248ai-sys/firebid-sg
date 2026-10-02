"""A bid's legends and symbols: what each maps to, confirming it, and what is counted.

The mapping screen reads the legend rows with their proposals, and the counts with every
unmapped symbol beside them. Confirming, correcting and rejecting need
`symbol_mapping.confirm`; a mapping is only reachable through a legend row or an instance of
this bid, so another bid's consultant's mappings are a 404 here.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from firebid.api.deps import CurrentBid, DbSession, require
from firebid.auth.permissions import Action
from firebid.auth.provisioning import Principal
from firebid.db.models.symbols import LegendEntry, SymbolInstance, SymbolMapping
from firebid.services import object_library
from firebid.services import symbols as service
from firebid.storage.object_store import get_object_store

router = APIRouter(prefix="/bids/{bid_id}/symbols", tags=["symbols"])


class MappingOut(BaseModel):
    lineage_id: uuid.UUID
    version: int
    state: str
    source: str
    object_type: str | None
    attributes: dict[str, Any]
    consultant: str
    project_only: bool
    description: str | None
    confidence: float | None = None
    model: str | None = None
    prompt_version: str | None = None
    rule_version: str | None = None
    reason: str | None = None
    confirmed_by: str | None
    change_note: str | None
    created_at: str


class LegendRowOut(BaseModel):
    id: uuid.UUID
    sheet_id: uuid.UUID
    heading: str
    description: str
    status: str
    symbol_box: list[float]
    has_crop: bool
    mapping: MappingOut | None


class CountedOut(BaseModel):
    object_type: str
    label: str
    count: int
    sheets: int


class UnmappedOut(BaseModel):
    symbol_key: str
    instances: int
    status: str
    description: str | None
    block: str | None
    mapping_lineage_id: uuid.UUID | None
    proposed_type: str | None
    sheet_ids: list[uuid.UUID]


class CountsOut(BaseModel):
    counted: list[CountedOut]
    unmapped: list[UnmappedOut]
    not_objects: int


class ObjectTypeChoice(BaseModel):
    key: str
    label: str
    category: str
    attribute_schema: dict[str, Any]
    measure: str = "count"  # count | length | none: which manual tool takes it off


class DecisionRequest(BaseModel):
    object_type: str | None = Field(default=None, description="omit to confirm as proposed")
    attributes: dict[str, Any] | None = None
    note: str | None = Field(default=None, max_length=2000)


class RejectRequest(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


def mapping_out(row: SymbolMapping) -> MappingOut:
    provenance = row.provenance or {}

    def text(name: str) -> str | None:
        value = provenance.get(name)
        return str(value) if value is not None else None

    confidence = provenance.get("confidence")
    return MappingOut(
        lineage_id=row.lineage_id,
        version=row.version,
        state=row.state,
        source=row.source,
        object_type=row.object_type_key,
        attributes=dict(row.attributes or {}),
        consultant=row.consultant,
        project_only=row.project_id is not None,
        description=row.description,
        confidence=float(confidence) if isinstance(confidence, int | float) else None,
        model=text("model"),
        prompt_version=text("prompt_version"),
        rule_version=text("rule_version"),
        reason=text("reason"),
        confirmed_by=row.confirmed_by,
        change_note=row.change_note,
        created_at=row.created_at.isoformat(),
    )


@router.get("/legend", response_model=list[LegendRowOut])
def legend_rows(context: CurrentBid, session: DbSession) -> list[LegendRowOut]:
    """Every legend row on the bid's sheets, with the mapping it resolves to."""
    rows = session.execute(
        select(LegendEntry)
        .where(LegendEntry.bid_id == context.bid.id)
        .order_by(LegendEntry.sheet_id, LegendEntry.ordinal)
    ).scalars()
    out = []
    for entry in rows:
        mapping = (
            service.current(session, entry.mapping_lineage_id) if entry.mapping_lineage_id else None
        )
        out.append(
            LegendRowOut(
                id=entry.id,
                sheet_id=entry.sheet_id,
                heading=entry.heading,
                description=entry.description,
                status=entry.status,
                symbol_box=entry.symbol_box,
                has_crop=entry.crop_key is not None,
                mapping=mapping_out(mapping) if mapping else None,
            )
        )
    return out


@router.get("/legend/{entry_id}/crop.png")
def legend_crop(entry_id: uuid.UUID, context: CurrentBid, session: DbSession) -> Response:
    entry = session.get(LegendEntry, entry_id)
    if entry is None or entry.bid_id != context.bid.id or entry.crop_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such legend row")
    return Response(
        content=get_object_store().get(entry.crop_key),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.get("/counts", response_model=CountsOut)
def symbol_counts(context: CurrentBid, session: DbSession) -> CountsOut:
    """Counts of confirmed, countable types on Current sheets; everything else as unmapped."""
    found = service.counts(session, context.bid.id)
    return CountsOut(
        counted=[
            CountedOut(
                object_type=item.object_type,
                label=item.label,
                count=item.count,
                sheets=len(item.sheets),
            )
            for item in sorted(found.counted.values(), key=lambda item: item.label)
        ],
        unmapped=[
            UnmappedOut(
                symbol_key=group.symbol_key,
                instances=group.instances,
                status=group.status,
                description=group.description,
                block=group.block,
                mapping_lineage_id=group.mapping_lineage_id,
                proposed_type=group.proposed_type,
                sheet_ids=sorted(group.sheets),
            )
            for group in found.unmapped
        ],
        not_objects=found.not_objects,
    )


@router.get("/object-types", response_model=list[ObjectTypeChoice])
def object_type_choices(context: CurrentBid, session: DbSession) -> list[ObjectTypeChoice]:
    """The types a symbol may be mapped to now."""
    object_library.ensure_seeded(session, context.bid.organisation_id)
    return [
        ObjectTypeChoice(
            key=t.key,
            label=t.label,
            category=t.category,
            attribute_schema=t.attribute_schema,
            measure=t.measure,
        )
        for t in object_library.usable(session, context.bid.organisation_id)
    ]


def _lineage_on_bid(session: DbSession, context: CurrentBid, lineage_id: uuid.UUID) -> None:
    """The mapping must be one this bid uses, through a legend row or an instance."""
    used = (
        session.execute(
            select(LegendEntry.id)
            .where(
                LegendEntry.bid_id == context.bid.id, LegendEntry.mapping_lineage_id == lineage_id
            )
            .limit(1)
        ).first()
        or session.execute(
            select(SymbolInstance.id)
            .where(
                SymbolInstance.bid_id == context.bid.id,
                or_(SymbolInstance.mapping_lineage_id == lineage_id),
            )
            .limit(1)
        ).first()
    )
    if not used:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such mapping on this bid")


@router.get("/mappings/{lineage_id}/history", response_model=list[MappingOut])
def mapping_history(
    lineage_id: uuid.UUID, context: CurrentBid, session: DbSession
) -> list[MappingOut]:
    _lineage_on_bid(session, context, lineage_id)
    return [mapping_out(row) for row in service.history(session, lineage_id)]


@router.post("/mappings/{lineage_id}/confirm", response_model=MappingOut)
def confirm_mapping(
    lineage_id: uuid.UUID,
    body: DecisionRequest,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.SYMBOL_MAPPING_CONFIRM)],
) -> MappingOut:
    """Say what the symbol is: the proposal as it stands, or corrected."""
    _lineage_on_bid(session, context, lineage_id)
    try:
        row = service.confirm(
            session,
            lineage_id,
            principal.actor(),
            object_type=body.object_type,
            attributes=body.attributes,
            note=body.note,
            bid_id=context.bid.id,
        )
    except service.MappingError as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal
    _detect_again(session, context, principal)
    return mapping_out(row)


def _detect_again(session: DbSession, context: CurrentBid, principal: Principal) -> None:
    """What a symbol is decides whether it is detected: detect the bid's sheets again.

    One waiting job serves every decision made before it starts: a person confirming a
    legend row by row queues one detection, not one for each row.
    """
    from firebid.jobs.enqueue import enqueue_once
    from firebid.jobs.tasks import run_detection

    enqueue_once(
        session,
        run_detection,
        f"detection.run:{context.bid.id}",
        bid_id=str(context.bid.id),
        user_id=str(principal.user_id),
    )


class NameUnlistedRequest(BaseModel):
    symbol_key: str
    object_type: str
    note: str | None = None


@router.post("/unlisted", response_model=MappingOut)
def name_unlisted(
    body: NameUnlistedRequest,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.SYMBOL_MAPPING_CONFIRM)],
) -> MappingOut:
    """Say what a recurring symbol no legend explains is, often "not an installed object"."""
    try:
        row = service.name_unlisted(
            session,
            context.bid.id,
            body.symbol_key,
            body.object_type,
            principal.actor(),
            body.note,
        )
    except service.MappingError as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal
    _detect_again(session, context, principal)
    return mapping_out(row)


@router.post("/mappings/{lineage_id}/reject", response_model=MappingOut)
def reject_mapping(
    lineage_id: uuid.UUID,
    body: RejectRequest,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.SYMBOL_MAPPING_CONFIRM)],
) -> MappingOut:
    _lineage_on_bid(session, context, lineage_id)
    try:
        row = service.reject(
            session, lineage_id, principal.actor(), note=body.note, bid_id=context.bid.id
        )
    except service.MappingError as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal
    _detect_again(session, context, principal)
    return mapping_out(row)
