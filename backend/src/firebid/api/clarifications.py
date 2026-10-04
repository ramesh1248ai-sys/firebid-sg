"""Tender clarifications (FR-RFI-01, 02, 04, 05, 06, 07; P2-06).

* `GET  /bids/{id}/clarifications/candidates`: what is flagged, in proposed groups.
* `POST /bids/{id}/clarifications`: draft one from a candidate or a confirmed group.
* `GET  /bids/{id}/clarifications` (the register), `GET .../{cid}`, `PATCH .../{cid}`.
* `POST .../{cid}/transition`, `.../design-approval`, `.../draft-with-model`,
  `.../response` (a file), `.../impact`.
* `GET  /bids/{id}/clarifications/export`: the register as a download, in a template.
* `POST /bids/{id}/clarifications/prepare-submission`; `GET /bids/{id}/qualifications`,
  `POST /bids/{id}/qualifications/{qid}/decide`.

The platform sends nothing to a client. The export is handed to the signed-in person who
asked for it, and a person records that a clarification was issued.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field

from firebid.api.deps import CurrentBid, DbSession
from firebid.clarifications import export
from firebid.db.models.clarifications import Clarification, Qualification
from firebid.db.models.core import Bid
from firebid.ingest.scanning import get_scanner
from firebid.services import clarifications as service
from firebid.storage.object_store import get_object_store

router = APIRouter(prefix="/bids/{bid_id}", tags=["clarifications"])

MAX_RESPONSE_BYTES = 50 * 1024 * 1024


def refused(error: Exception, code: int = status.HTTP_409_CONFLICT) -> HTTPException:
    return HTTPException(code, str(error))


class EvidenceOut(BaseModel):
    kind: str
    label: str
    quote: str = ""
    link: str | None = None
    revision: str | None = None


class SheetRefOut(BaseModel):
    sheet_number: str
    revision: str | None = None
    sheet_id: str | None = None


class CandidateOut(BaseModel):
    kind: str
    ref: str
    subject: str
    problem: str
    evidence: list[EvidenceOut]
    system: str
    level_grid: str
    sheets: list[SheetRefOut]
    options: list[str]
    topic: str


class CandidateGroupOut(BaseModel):
    key: str
    reason: str
    candidates: list[CandidateOut]


class CandidatesOut(BaseModel):
    groups: list[CandidateGroupOut]
    # FR-RFI-01: the only kind of clarification the platform raises.
    kinds: list[str]


class OptionOut(BaseModel):
    text: str
    recommendation: bool = True


class SourceOut(BaseModel):
    kind: str
    ref: str


class ClarificationOut(BaseModel):
    id: uuid.UUID
    kind: str
    number: str
    subject: str
    project: str
    level_grid: str
    sheets: list[SheetRefOut]
    problem: str
    evidence: list[EvidenceOut]
    options: list[OptionOut]
    cost_impact: str
    programme_impact: str
    required_reviewer: str
    engineering_content: bool
    engineering_reason: str
    qp_input_needed: bool
    state: str
    state_label: str
    due_at: datetime | None
    overdue: bool
    drafting: dict[str, Any]
    design_approved_by: str | None
    design_approved_at: datetime | None
    design_note: str | None
    approved_by: str | None
    approved_at: datetime | None
    issued_by: str | None
    issued_at: datetime | None
    responded_at: datetime | None
    response_summary: str | None
    response_document_id: uuid.UUID | None
    impact_task_id: uuid.UUID | None
    impact_outcome: str | None
    impact_note: str | None
    impact_detail: dict[str, Any]
    impact_by: str | None
    sources: list[SourceOut]
    created_at: datetime


class ClarificationTemplateOut(BaseModel):
    key: str
    label: str
    headings: list[str]


class RegisterOut(BaseModel):
    clarification_cutoff: datetime | None
    due_at: datetime | None
    clarifications: list[ClarificationOut]
    templates: list[ClarificationTemplateOut]


class PickIn(BaseModel):
    kind: str = Field(max_length=24)
    ref: str = Field(max_length=200)


class DraftIn(BaseModel):
    candidates: list[PickIn] = Field(min_length=1, max_length=40)
    # Reserved: only "tender_clarification" is accepted (FR-RFI-01).
    kind: str = "tender_clarification"


class ClarificationEditIn(BaseModel):
    subject: str | None = Field(default=None, min_length=1, max_length=300)
    level_grid: str | None = Field(default=None, max_length=200)
    problem: str | None = Field(default=None, min_length=1, max_length=6000)
    options: list[str] | None = None
    cost_impact: str | None = Field(default=None, max_length=2000)
    programme_impact: str | None = Field(default=None, max_length=2000)


class ClarificationTransitionIn(BaseModel):
    target: str = Field(max_length=32)


class DesignApprovalIn(BaseModel):
    qp_input_needed: bool = False
    note: str | None = Field(default=None, max_length=2000)


class ImpactIn(BaseModel):
    outcome: str = Field(pattern="^(incorporated|no_change)$")
    note: str = Field(min_length=1, max_length=4000)
    rerun: bool = False


class QualificationOut(BaseModel):
    id: uuid.UUID
    kind: str
    text: str
    clarification_id: uuid.UUID | None
    clarification_number: str | None
    state: str
    decided_by: str | None
    decided_at: datetime | None
    note: str | None


class QualificationDecisionIn(BaseModel):
    decision: str = Field(pattern="^(accepted|rejected)$")
    text: str | None = Field(default=None, max_length=6000)
    note: str | None = Field(default=None, max_length=2000)


def clarification_out(session: DbSession, row: Clarification) -> ClarificationOut:
    return ClarificationOut(
        id=row.id,
        kind=row.kind,
        number=service.label(row),
        subject=row.subject,
        project=row.project,
        level_grid=row.level_grid,
        sheets=[SheetRefOut(**sheet) for sheet in row.sheets or []],
        problem=row.problem,
        evidence=[EvidenceOut(**item) for item in row.evidence or []],
        options=[OptionOut(**option) for option in row.options or []],
        cost_impact=row.cost_impact,
        programme_impact=row.programme_impact,
        required_reviewer=row.required_reviewer,
        engineering_content=row.engineering_content,
        engineering_reason=row.engineering_reason,
        qp_input_needed=row.qp_input_needed,
        state=row.state,
        state_label=service.STATE_WORDS[row.state],
        due_at=row.due_at,
        overdue=service.overdue(row),
        drafting=dict(row.drafting or {}),
        design_approved_by=row.design_approved_by,
        design_approved_at=row.design_approved_at,
        design_note=row.design_note,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        issued_by=row.issued_by,
        issued_at=row.issued_at,
        responded_at=row.responded_at,
        response_summary=row.response_summary,
        response_document_id=row.response_document_id,
        impact_task_id=row.impact_task_id,
        impact_outcome=row.impact_outcome,
        impact_note=row.impact_note,
        impact_detail=dict(row.impact_detail or {}),
        impact_by=row.impact_by,
        sources=[
            SourceOut(kind=source.kind, ref=source.ref) for source in service.sources(session, row)
        ],
        created_at=row.created_at,
    )


def _one(session: DbSession, bid: Bid, clarification_id: uuid.UUID) -> Clarification:
    row = session.get(Clarification, clarification_id)
    if row is None or row.bid_id != bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such clarification")
    return row


@router.get("/clarifications/candidates", response_model=CandidatesOut)
def candidates(context: CurrentBid, session: DbSession) -> CandidatesOut:
    """What the platform has flagged that only the client can settle, in the groups it
    proposes: related issues to be asked as one clarification, once a person confirms."""
    return CandidatesOut(
        groups=[
            CandidateGroupOut(
                key=group.key,
                reason=group.reason,
                candidates=[CandidateOut(**item.as_json()) for item in group.candidates],
            )
            for group in service.proposed_groups(session, context.bid)
        ],
        kinds=["tender_clarification"],
    )


@router.get("/clarifications", response_model=RegisterOut)
def register(context: CurrentBid, session: DbSession) -> RegisterOut:
    """The register: every clarification with its state and when it is due."""
    return RegisterOut(
        clarification_cutoff=context.bid.clarification_cutoff,
        due_at=service.due_date(context.bid),
        clarifications=[
            clarification_out(session, row) for row in service.register(session, context.bid.id)
        ],
        templates=[
            ClarificationTemplateOut(
                key=item.key, label=item.label, headings=[h for h, _ in item.columns]
            )
            for item in export.templates()
        ],
    )


@router.post("/clarifications", response_model=ClarificationOut, status_code=201)
def draft(body: DraftIn, context: CurrentBid, session: DbSession) -> ClarificationOut:
    """Draft one clarification from a candidate, or from the candidates of a group the
    person confirmed. A draft with no evidence to cite is refused and not saved."""
    try:
        row = service.draft(
            session,
            context.bid,
            [(pick.kind, pick.ref) for pick in body.candidates],
            context.principal.actor(),
            kind=body.kind,
        )
    except service.ClarificationError as refusal:
        raise refused(refusal, status.HTTP_422_UNPROCESSABLE_CONTENT) from refusal
    return clarification_out(session, row)


@router.get("/clarifications/export")
def export_register(
    context: CurrentBid,
    session: DbSession,
    template: str = "company_default",
    format: str = "xlsx",
    only: str | None = None,
) -> Response:
    """The register as a file, in the company's form or a client's layout. This download,
    by the person signed in, is the only way a clarification leaves the platform."""
    try:
        content = service.export_register(
            session,
            context.bid,
            context.principal.actor(),
            template=template,
            kind=format,
            only=only,
        )
    except service.ClarificationError as refusal:
        raise refused(refusal, status.HTTP_422_UNPROCESSABLE_CONTENT) from refusal
    name = f"{context.bid.human_id} clarifications.{format}"
    return Response(
        content,
        media_type=export.FORMATS[format],
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/clarifications/prepare-submission", response_model=list[QualificationOut])
def prepare_submission(context: CurrentBid, session: DbSession) -> list[QualificationOut]:
    """At submission: every unresolved clarification becomes a proposed qualification or
    assumption, linked back to it, for review."""
    try:
        service.prepare_submission(session, context.bid, context.principal.actor())
    except service.ClarificationError as refusal:
        raise refused(refusal) from refusal
    return _qualifications(session, context.bid)


@router.get("/clarifications/{clarification_id}", response_model=ClarificationOut)
def get_one(
    clarification_id: uuid.UUID, context: CurrentBid, session: DbSession
) -> ClarificationOut:
    return clarification_out(session, _one(session, context.bid, clarification_id))


@router.patch("/clarifications/{clarification_id}", response_model=ClarificationOut)
def edit(
    clarification_id: uuid.UUID, body: ClarificationEditIn, context: CurrentBid, session: DbSession
) -> ClarificationOut:
    row = _one(session, context.bid, clarification_id)
    try:
        service.edit(
            session,
            context.bid,
            row,
            body.model_dump(exclude_unset=True),
            context.principal.actor(),
        )
    except service.ClarificationError as refusal:
        raise refused(refusal) from refusal
    return clarification_out(session, row)


@router.post("/clarifications/{clarification_id}/transition", response_model=ClarificationOut)
def transition(
    clarification_id: uuid.UUID,
    body: ClarificationTransitionIn,
    context: CurrentBid,
    session: DbSession,
) -> ClarificationOut:
    """Send for review, approve to issue (the Bid Manager), or record as issued."""
    row = _one(session, context.bid, clarification_id)
    try:
        service.transition(session, context.bid, row, body.target, context.principal.actor())
    except service.ClarificationError as refusal:
        raise refused(refusal) from refusal
    return clarification_out(session, row)


@router.post("/clarifications/{clarification_id}/design-approval", response_model=ClarificationOut)
def design_approval(
    clarification_id: uuid.UUID, body: DesignApprovalIn, context: CurrentBid, session: DbSession
) -> ClarificationOut:
    """The Design Manager approves the engineering content, and says whether the QP's input
    is needed."""
    row = _one(session, context.bid, clarification_id)
    try:
        service.design_approval(
            session,
            context.bid,
            row,
            context.principal.actor(),
            qp_input_needed=body.qp_input_needed,
            note=body.note,
        )
    except service.ClarificationError as refusal:
        raise refused(refusal, status.HTTP_403_FORBIDDEN) from refusal
    return clarification_out(session, row)


@router.post("/clarifications/{clarification_id}/draft-with-model", response_model=ClarificationOut)
def draft_with_model(
    clarification_id: uuid.UUID, context: CurrentBid, session: DbSession
) -> ClarificationOut:
    """Ask the model to word the draft more clearly, from the evidence it already cites."""
    from firebid.ai_gateway import gateway

    row = _one(session, context.bid, clarification_id)
    try:
        service.draft_with_model(session, context.bid, row, gateway(), context.principal.actor())
    except service.ClarificationError as refusal:
        raise refused(refusal) from refusal
    return clarification_out(session, row)


@router.post("/clarifications/{clarification_id}/response", response_model=ClarificationOut)
async def record_response(
    clarification_id: uuid.UUID,
    context: CurrentBid,
    session: DbSession,
    summary: Annotated[str, Form(min_length=1, max_length=6000)],
    file: Annotated[UploadFile | None, File()] = None,
) -> ClarificationOut:
    """The client's response: what it says, and its file, kept with the tender documents.
    A task to assess its impact is raised."""
    row = _one(session, context.bid, clarification_id)
    payload = await file.read(MAX_RESPONSE_BYTES + 1) if file is not None else None
    if payload is not None and len(payload) > MAX_RESPONSE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "a response is at most 50 MB")
    try:
        service.record_response(
            session,
            context.bid,
            row,
            context.principal.actor(),
            summary=summary,
            payload=payload,
            filename=file.filename if file is not None else None,
            store=get_object_store(),
            scanner=get_scanner(),
        )
    except service.ClarificationError as refusal:
        raise refused(refusal) from refusal
    return clarification_out(session, row)


@router.post("/clarifications/{clarification_id}/impact", response_model=ClarificationOut)
def assess_impact(
    clarification_id: uuid.UUID, body: ImpactIn, context: CurrentBid, session: DbSession
) -> ClarificationOut:
    """Say what the response changed: incorporated, or no change. With `rerun`, takeoff and
    pricing are run again first and what they changed is recorded."""
    row = _one(session, context.bid, clarification_id)
    try:
        service.assess_impact(
            session,
            context.bid,
            row,
            context.principal.actor(),
            outcome=body.outcome,
            note=body.note,
            rerun=body.rerun,
        )
    except service.ClarificationError as refusal:
        raise refused(refusal) from refusal
    return clarification_out(session, row)


def _qualifications(session: DbSession, bid: Bid) -> list[QualificationOut]:
    numbers = {row.id: service.label(row) for row in service.register(session, bid.id)}
    return [
        QualificationOut(
            id=row.id,
            kind=row.kind,
            text=row.text,
            clarification_id=row.clarification_id,
            clarification_number=numbers.get(row.clarification_id)
            if row.clarification_id
            else None,
            state=row.state,
            decided_by=row.decided_by,
            decided_at=row.decided_at,
            note=row.note,
        )
        for row in service.qualifications(session, bid.id)
    ]


@router.get("/qualifications", response_model=list[QualificationOut])
def qualifications(context: CurrentBid, session: DbSession) -> list[QualificationOut]:
    """Proposed qualifications and assumptions, each linked to the clarification it is from."""
    return _qualifications(session, context.bid)


@router.post("/qualifications/{qualification_id}/decide", response_model=list[QualificationOut])
def decide_qualification(
    qualification_id: uuid.UUID,
    body: QualificationDecisionIn,
    context: CurrentBid,
    session: DbSession,
) -> list[QualificationOut]:
    row = session.get(Qualification, qualification_id)
    if row is None or row.bid_id != context.bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such qualification")
    try:
        service.decide_qualification(
            session,
            context.bid,
            row,
            context.principal.actor(),
            decision=body.decision,
            text=body.text,
            note=body.note,
        )
    except service.ClarificationError as refusal:
        raise refused(refusal) from refusal
    return _qualifications(session, context.bid)
