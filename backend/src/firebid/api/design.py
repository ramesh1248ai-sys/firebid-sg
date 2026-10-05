"""Design development for a design-intent tender (P1-12).

A bid's plan sheets, each with the design criteria its notes state. A person with
`design_basis.confirm` chooses the criterion; only then is a layout proposed. The proposed
heads and pipes are read through the detections and review routes like any others, and the
takeoff counts them as items of their own.

Nothing here is a design approval: the layout is an estimating aid (ADR-011).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field, model_validator

from firebid.api.deps import CurrentBid, DbSession, require
from firebid.auth.permissions import Action
from firebid.auth.provisioning import Principal
from firebid.db.models.design import SheetDesign
from firebid.services import design as design_service
from firebid.services import qto as qto_service
from firebid.storage.object_store import get_object_store

router = APIRouter(prefix="/bids/{bid_id}/design", tags=["design"])

Point = Annotated[list[float], Field(min_length=2, max_length=2)]


class CriterionOut(BaseModel):
    key: str
    title: str
    max_area_m2: float
    max_spacing_mm: list[int]
    source: str
    k_factor: str | None = None
    response: str | None = None


class SpaceOut(BaseModel):
    index: int
    kind: str
    name: str
    area_m2: float
    heads: int
    pitch_mm: list[int]
    omitted_by: str | None
    note: str | None
    box: list[float]


class MatchLineOut(BaseModel):
    label: str
    other_sheet: str | None = None
    # The line across the plan, in sheet millimetres. None: the label's line was not found.
    line: list[list[float]] | None = None
    side: int | None = None
    proposed_side: int | None = None
    reason: str | None = None


class SheetDesignOut(BaseModel):
    id: uuid.UUID
    sheet_id: uuid.UUID
    sheet_number: str
    level: str | None
    view_id: uuid.UUID | None
    state: str
    note: str | None
    design_intent: bool
    intent_quote: str | None
    drawn_heads: int
    criteria: list[CriterionOut]
    criterion: CriterionOut | None
    scope: list[list[float]] | None
    scope_source: str | None
    match_lines: list[MatchLineOut]
    rule_version: int | None
    totals: dict[str, Any]
    spaces: list[SpaceOut]
    laid_out_at: datetime | None
    confirmed_by: str | None
    confirmed_at: datetime | None


class DesignOut(BaseModel):
    rule_version: int
    rule_status: str
    sheets: list[SheetDesignOut]


class EnteredCriterion(BaseModel):
    max_area_m2: float = Field(gt=0, le=50)
    max_spacing_mm: Annotated[list[int], Field(min_length=2, max_length=2)]
    title: str = Field(default="entered criterion", max_length=200)


class ConfirmRequest(BaseModel):
    sheet_ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=2000)]
    key: str | None = None
    entered: EnteredCriterion | None = None

    @model_validator(mode="after")
    def one_choice(self) -> ConfirmRequest:
        if (self.key is None) == (self.entered is None):
            raise ValueError("choose one of the sheet's criteria, or enter one")
        return self


class ConfirmOut(BaseModel):
    confirmed: list[uuid.UUID]
    skipped: dict[uuid.UUID, str]


class ScopeRequest(BaseModel):
    """One of: a polygon drawn by hand; the side of each match line (by its place in the
    sheet's list, +1 or -1); the match lines' own proposal again. None of them: the whole
    sheet."""

    polygon: Annotated[list[Point], Field(min_length=3, max_length=200)] | None = None
    sides: dict[int, int] | None = None
    follow_match_lines: bool = False

    @model_validator(mode="after")
    def one_choice(self) -> ScopeRequest:
        chosen = [self.polygon is not None, self.sides is not None, self.follow_match_lines]
        if sum(chosen) > 1:
            raise ValueError("give a polygon, or sides, or follow the match lines: one of them")
        return self


class ExportOut(BaseModel):
    """One format's export: none, queued, ready, stale (the layout changed) or failed."""

    format: str
    state: str
    bytes: int | None = None
    made_at: str | None = None
    reason: str | None = None


class WithdrawRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


def _out(row: SheetDesign, number: str, level: str | None) -> SheetDesignOut:
    return SheetDesignOut(
        id=row.id,
        sheet_id=row.sheet_id,
        sheet_number=number,
        level=level,
        view_id=row.view_id,
        state=row.state,
        note=row.note,
        design_intent=row.design_intent,
        intent_quote=row.intent_quote,
        drawn_heads=row.drawn_heads,
        criteria=[CriterionOut.model_validate(c) for c in row.criteria or []],
        criterion=CriterionOut.model_validate(row.criterion) if row.criterion else None,
        scope=row.scope,
        scope_source=row.scope_source,
        match_lines=[MatchLineOut.model_validate(line) for line in row.match_lines or []],
        rule_version=row.rule_version,
        totals=dict(row.totals or {}),
        spaces=[SpaceOut.model_validate(s) for s in row.spaces or []],
        laid_out_at=row.laid_out_at,
        confirmed_by=row.confirmed_by,
        confirmed_at=row.confirmed_at,
    )


def _row(session: DbSession, context: CurrentBid, sheet_id: uuid.UUID) -> SheetDesign:
    row = design_service.designs(session, context.bid.id).get(sheet_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no design basis for this sheet")
    return row


@router.get("", response_model=DesignOut)
def list_designs(context: CurrentBid, session: DbSession) -> DesignOut:
    """Each plan sheet's design basis and proposed layout, in sheet order."""
    rule = design_service.rules_row(session, context.bid.organisation_id)
    sheets = qto_service.current_sheets(session, context.bid.id)
    rows = design_service.designs(session, context.bid.id)
    listed = [
        _out(row, sheets[sheet_id].number, sheets[sheet_id].level)
        for sheet_id, row in rows.items()
        if sheet_id in sheets
    ]
    return DesignOut(
        rule_version=rule.version,
        rule_status=rule.status,
        sheets=sorted(listed, key=lambda item: item.sheet_number),
    )


@router.post("/basis", status_code=202)
def read_basis(
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.QTO_EDIT)],
) -> dict[str, str]:
    """Queue reading every Current plan sheet's design basis."""
    from firebid.jobs.enqueue import enqueue
    from firebid.jobs.tasks import read_design_basis

    enqueue(session, read_design_basis, bid_id=str(context.bid.id), user_id=str(principal.user_id))
    return {"status": "queued"}


@router.post("/confirm", response_model=ConfirmOut)
def confirm_basis(
    body: ConfirmRequest,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.DESIGN_BASIS_CONFIRM)],
) -> ConfirmOut:
    """Confirm the criterion for these sheets and queue their layout."""
    entered = (
        design_service.Entered(
            body.entered.max_area_m2,
            (body.entered.max_spacing_mm[0], body.entered.max_spacing_mm[1]),
            body.entered.title,
        )
        if body.entered
        else None
    )
    try:
        outcome = design_service.confirm(
            session,
            context.bid.id,
            body.sheet_ids,
            principal.actor(),
            key=body.key,
            entered=entered,
        )
    except design_service.DesignError as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal
    return ConfirmOut(
        confirmed=[row.sheet_id for row in outcome.confirmed], skipped=outcome.skipped
    )


@router.post("/sheets/{sheet_id}/withdraw", status_code=204)
def withdraw_design(
    sheet_id: uuid.UUID,
    body: WithdrawRequest,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.DESIGN_BASIS_CONFIRM)],
) -> None:
    """Remove a sheet's proposed layout from the bid."""
    try:
        design_service.withdraw(
            session, _row(session, context, sheet_id), principal.actor(), body.reason
        )
    except design_service.DesignError as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal


@router.put("/sheets/{sheet_id}/scope", status_code=204)
def set_scope(
    sheet_id: uuid.UUID,
    body: ScopeRequest,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.QTO_EDIT)],
) -> None:
    """The part of the plan this sheet answers for: its side of its match lines
    (FR-DSN-06), as proposed, as a person chooses, or as a polygon drawn by hand."""
    row = _row(session, context, sheet_id)
    try:
        if body.sides is not None:
            design_service.choose_sides(session, row, body.sides, principal.actor())
        elif body.follow_match_lines:
            design_service.follow_match_lines(session, row, principal.actor())
        else:
            polygon = [(p[0], p[1]) for p in body.polygon] if body.polygon else None
            design_service.set_scope(session, row, polygon, principal.actor())
    except design_service.DesignError as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal


@router.post("/sheets/{sheet_id}/export", response_model=ExportOut, status_code=202)
def request_export(
    sheet_id: uuid.UUID, context: CurrentBid, session: DbSession, format: str = "pdf"
) -> ExportOut:
    """Ask for the sheet's proposed layout over its tender drawing, as a PDF or a DXF
    (FR-DSN-05). A real sheet takes tens of seconds to draw, so a job makes the file and
    keeps it; one already made from the layout as it stands is ready at once."""
    try:
        found = design_service.request_export(
            session, _row(session, context, sheet_id), format.lower(), context.principal.actor()
        )
    except design_service.DesignError as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal
    return ExportOut.model_validate(found)


@router.get("/sheets/{sheet_id}/export/status", response_model=list[ExportOut])
def export_status(sheet_id: uuid.UUID, context: CurrentBid, session: DbSession) -> list[ExportOut]:
    """Where each format's export stands."""
    found = design_service.exports_of(session, _row(session, context, sheet_id))
    return [ExportOut.model_validate(entry) for entry in found.values()]


@router.get("/sheets/{sheet_id}/export")
def export_layout(
    sheet_id: uuid.UUID, context: CurrentBid, session: DbSession, format: str = "pdf"
) -> Response:
    """The file once it is made: stamped "For estimation only: not for construction". A
    download for the person asking; the platform sends it nowhere."""
    try:
        exported = design_service.export_file(
            session,
            get_object_store(),
            _row(session, context, sheet_id),
            format.lower(),
            context.principal.actor(),
        )
    except design_service.DesignError as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal
    headers = {"Content-Disposition": f'attachment; filename="{exported.filename}"'}
    if exported.encoding:
        # Kept compressed; the browser unpacks it as it saves the file.
        headers["Content-Encoding"] = exported.encoding
    return Response(exported.content, media_type=exported.media_type, headers=headers)
