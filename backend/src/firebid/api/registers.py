"""The drawing and specification registers, and the decisions people make on them (FR-DOC-03).

Reading a register needs only membership of the bid. Every decision needs a role:
`document.review` to confirm a reading or a type, resolve a conflict or withdraw an entry,
and `register.confirm` for the Estimator's "Register confirmed". Each decision goes through
the revision state machine or writes an audit event, never straight to a column.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from firebid.api.deps import CurrentBid, DbSession, require
from firebid.auth.permissions import Action, may
from firebid.auth.provisioning import Principal
from firebid.db.models.documents import (
    Document,
    DocumentRevision,
    RegisterConfirmation,
    SheetRevision,
)
from firebid.domain.state_machines import SheetRevisionState, TransitionError
from firebid.ingest.classification import DocType
from firebid.services import registers
from firebid.services.revisions import resolve_conflict

router = APIRouter(prefix="/bids/{bid_id}", tags=["registers"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class DrawingRowOut(BaseModel):
    revision_id: uuid.UUID
    sheet_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    sheet_number: str | None
    title: str | None
    revision: str | None
    revision_date: date | None
    discipline: str | None
    level: str | None
    zone: str | None
    scale: str | None
    state: str
    confidence: float | None
    read_by: str | None
    conflict_reason: str | None
    addendum: str | None
    content_class: str | None
    quality_band: str | None
    manual_takeoff_recommended: bool

    model_config = {"from_attributes": True}


class DocumentRowOut(BaseModel):
    revision_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    doc_type: str | None
    type_confidence: float | None
    type_decided_by: str | None
    doc_key: str | None
    title: str | None
    revision: str | None
    revision_date: date | None
    state: str
    conflict_reason: str | None
    addendum: str | None

    model_config = {"from_attributes": True}


class ConfirmationOut(BaseModel):
    confirmed_by_id: uuid.UUID
    confirmed_role: str
    confirmed_at: str
    snapshot_hash: str
    drawings: int
    documents: int
    comment: str | None


class RegisterStatus(BaseModel):
    conflicts: int
    unidentified: int
    unsure_types: int
    ready: bool
    confirmation: ConfirmationOut | None


class ConfirmRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


class ReadingRequest(BaseModel):
    sheet_number: str = Field(min_length=1, max_length=120)
    revision: str = Field(min_length=1, max_length=40)
    title: str | None = Field(default=None, max_length=300)
    consultant: str | None = Field(
        default=None,
        max_length=200,
        description="Remember this title block layout for this consultant's later sheets.",
    )


class ResolveRequest(BaseModel):
    outcome: Literal["current", "superseded", "withdrawn"]
    reason: str = Field(min_length=3, max_length=2000)
    revision: str | None = Field(default=None, max_length=40)


class WithdrawRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class IdentifyRequest(BaseModel):
    document: str = Field(min_length=1, max_length=200)
    revision: str | None = Field(default=None, max_length=40)


class TypeRequest(BaseModel):
    doc_type: DocType


# --- Registers -------------------------------------------------------------------------------


@router.get("/registers/drawings", response_model=list[DrawingRowOut])
def drawings(
    context: CurrentBid,
    session: DbSession,
    discipline: Annotated[str | None, Query(max_length=40)] = None,
    level: Annotated[str | None, Query(max_length=40)] = None,
    state: Annotated[str | None, Query(max_length=24)] = None,
) -> list[DrawingRowOut]:
    rows = registers.drawing_register(
        session, context.bid.id, discipline=discipline, level=level, state=state
    )
    return [DrawingRowOut.model_validate(row) for row in rows]


@router.get("/registers/documents", response_model=list[DocumentRowOut])
def documents(
    context: CurrentBid,
    session: DbSession,
    doc_type: Annotated[str | None, Query(max_length=32)] = None,
    state: Annotated[str | None, Query(max_length=24)] = None,
) -> list[DocumentRowOut]:
    rows = registers.document_register(session, context.bid.id, doc_type=doc_type, state=state)
    return [DocumentRowOut.model_validate(row) for row in rows]


@router.get("/registers/{kind}.xlsx", response_class=Response)
def export(
    kind: Literal["drawings", "documents"],
    context: CurrentBid,
    session: DbSession,
) -> Response:
    """Download a register as a workbook. A named user's explicit action (guardrail 7)."""
    rows: list[registers.DrawingRow] | list[registers.DocumentRow] = (
        registers.drawing_register(session, context.bid.id)
        if kind == "drawings"
        else registers.document_register(session, context.bid.id)
    )
    payload = registers.export_xlsx(
        kind, rows, bid_reference=context.bid.human_id, by=context.principal.display_name
    )
    filename = f"{context.bid.human_id} {registers.REGISTER_TITLES[kind]}.xlsx"
    return Response(
        content=payload,
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/registers/status", response_model=RegisterStatus)
def register_status(context: CurrentBid, session: DbSession) -> RegisterStatus:
    found = registers.blockers(session, context.bid.id)
    confirmation = registers.latest_confirmation(session, context.bid.id)
    return RegisterStatus(
        conflicts=found.conflicts,
        unidentified=found.unidentified,
        unsure_types=found.unsure_types,
        ready=not found.any,
        confirmation=_confirmation_out(confirmation),
    )


@router.post("/registers/confirm", response_model=ConfirmationOut)
def confirm(
    body: ConfirmRequest,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.REGISTER_CONFIRM)],
) -> ConfirmationOut:
    """The Estimator's "Register confirmed": stage S1's output (requirements §5)."""
    role = next(role for role in sorted(principal.roles) if may({role}, Action.REGISTER_CONFIRM))
    try:
        confirmation = registers.confirm_registers(
            session, context.bid, actor=principal.actor(), role=role, comment=body.comment
        )
    except registers.RegisterNotReady as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal
    return _confirmation(confirmation)


def _confirmation_out(
    confirmation: RegisterConfirmation | None,
) -> ConfirmationOut | None:
    return None if confirmation is None else _confirmation(confirmation)


def _confirmation(confirmation: RegisterConfirmation) -> ConfirmationOut:
    return ConfirmationOut(
        confirmed_by_id=confirmation.confirmed_by_id,
        confirmed_role=confirmation.confirmed_role,
        confirmed_at=confirmation.confirmed_at.isoformat(),
        snapshot_hash=confirmation.snapshot_hash,
        drawings=confirmation.drawings,
        documents=confirmation.documents,
        comment=confirmation.comment,
    )


# --- Decisions on drawings -------------------------------------------------------------------


def _sheet_revision(
    session: DbSession, context: CurrentBid, revision_id: uuid.UUID
) -> SheetRevision:
    revision = session.get(SheetRevision, revision_id)
    if revision is None or revision.bid_id != context.bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet revision not found")
    return revision


def _document_revision(
    session: DbSession, context: CurrentBid, revision_id: uuid.UUID
) -> DocumentRevision:
    revision = session.get(DocumentRevision, revision_id)
    if revision is None or revision.bid_id != context.bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document revision not found")
    return revision


@router.post("/sheet-revisions/{revision_id}/confirm", response_model=DrawingRowOut)
def confirm_reading(
    revision_id: uuid.UUID,
    body: ReadingRequest,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.DOCUMENT_REVIEW)],
) -> DrawingRowOut:
    """Say what a sheet is: its number and revision, confirmed or corrected."""
    revision = _sheet_revision(session, context, revision_id)
    try:
        registers.confirm_reading(
            session,
            revision,
            actor=principal.actor(),
            sheet_number=body.sheet_number,
            revision_label=body.revision,
            title=body.title,
            consultant=body.consultant,
        )
    except (ValueError, TransitionError) as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal
    return _drawing_row(session, context, revision)


@router.post("/sheet-revisions/{revision_id}/resolve", response_model=DrawingRowOut)
def resolve_sheet(
    revision_id: uuid.UUID,
    body: ResolveRequest,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.DOCUMENT_REVIEW)],
) -> DrawingRowOut:
    revision = _sheet_revision(session, context, revision_id)
    _resolve(session, revision, body, principal)
    return _drawing_row(session, context, revision)


@router.post("/sheet-revisions/{revision_id}/withdraw", response_model=DrawingRowOut)
def withdraw_sheet(
    revision_id: uuid.UUID,
    body: WithdrawRequest,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.DOCUMENT_REVIEW)],
) -> DrawingRowOut:
    revision = _sheet_revision(session, context, revision_id)
    try:
        registers.withdraw(session, revision, actor=principal.actor(), reason=body.reason)
    except TransitionError as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal
    return _drawing_row(session, context, revision)


def _resolve(
    session: DbSession,
    revision: SheetRevision | DocumentRevision,
    body: ResolveRequest,
    principal: Principal,
) -> None:
    try:
        resolve_conflict(
            session,
            revision,
            outcome=SheetRevisionState(body.outcome),
            actor=principal.actor(),
            reason=body.reason,
            revision_label=body.revision,
        )
    except (ValueError, TransitionError) as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal


def _drawing_row(session: DbSession, context: CurrentBid, revision: SheetRevision) -> DrawingRowOut:
    for row in registers.drawing_register(session, context.bid.id):
        if row.revision_id == revision.id:
            return DrawingRowOut.model_validate(row)
    raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet revision not found")


# --- Decisions on documents ------------------------------------------------------------------


@router.post("/document-revisions/{revision_id}/identify", response_model=DocumentRowOut)
def identify_document(
    revision_id: uuid.UUID,
    body: IdentifyRequest,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.DOCUMENT_REVIEW)],
) -> DocumentRowOut:
    revision = _document_revision(session, context, revision_id)
    try:
        registers.identify_document(
            session,
            revision,
            actor=principal.actor(),
            doc_key=body.document,
            revision_label=body.revision,
        )
    except (ValueError, TransitionError) as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal
    return _document_row(session, context, revision)


@router.post("/document-revisions/{revision_id}/resolve", response_model=DocumentRowOut)
def resolve_document(
    revision_id: uuid.UUID,
    body: ResolveRequest,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.DOCUMENT_REVIEW)],
) -> DocumentRowOut:
    revision = _document_revision(session, context, revision_id)
    _resolve(session, revision, body, principal)
    return _document_row(session, context, revision)


@router.post("/document-revisions/{revision_id}/withdraw", response_model=DocumentRowOut)
def withdraw_document(
    revision_id: uuid.UUID,
    body: WithdrawRequest,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.DOCUMENT_REVIEW)],
) -> DocumentRowOut:
    revision = _document_revision(session, context, revision_id)
    try:
        registers.withdraw(session, revision, actor=principal.actor(), reason=body.reason)
    except TransitionError as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal
    return _document_row(session, context, revision)


@router.post("/documents/{document_id}/type", response_model=list[DocumentRowOut])
def confirm_type(
    document_id: uuid.UUID,
    body: TypeRequest,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.DOCUMENT_REVIEW)],
) -> list[DocumentRowOut]:
    """Confirm or correct what kind of document a file is."""
    document = session.get(Document, document_id)
    if document is None or document.bid_id != context.bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    registers.confirm_document_type(session, document, body.doc_type, actor=principal.actor())
    return [
        DocumentRowOut.model_validate(row)
        for row in registers.document_register(session, context.bid.id)
        if row.document_id == document.id
    ]


def _document_row(
    session: DbSession, context: CurrentBid, revision: DocumentRevision
) -> DocumentRowOut:
    for row in registers.document_register(session, context.bid.id):
        if row.revision_id == revision.id:
            return DocumentRowOut.model_validate(row)
    raise HTTPException(status.HTTP_404_NOT_FOUND, "document revision not found")
