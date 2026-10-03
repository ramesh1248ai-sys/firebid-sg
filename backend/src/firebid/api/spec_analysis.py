"""Obligations, cross-check issues and the scope matrix (FR-SPEC-02, 03, 04; P2-03).

* `POST /bids/{id}/spec/analysis/run`: read obligations, cross-check, build the matrix.
* `GET  /bids/{id}/spec/obligations`, `POST .../obligations/{id}/decide`.
* `GET  /bids/{id}/spec/issues`, `POST .../issues/{id}/decide`; `GET .../clarification-candidates`.
* `GET  /bids/{id}/spec/scope-matrix`, `POST .../rows/{id}`, `POST .../confirm`,
  `GET .../export.xlsx`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from firebid.api.deps import CurrentBid, DbSession, require
from firebid.auth.permissions import Action
from firebid.auth.provisioning import Principal
from firebid.db.models.specs import ScopeRow, SpecIssue, SpecObligation
from firebid.services import spec_analysis as analysis

router = APIRouter(prefix="/bids/{bid_id}/spec", tags=["specification"])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ObligationOut(BaseModel):
    id: uuid.UUID
    document_revision_id: uuid.UUID
    clause_id: uuid.UUID | None
    clause_number: str
    system: str
    category: str
    summary: str
    quantities: dict[str, Any]
    quote: str
    method: str
    confidence: float
    citation_ok: bool
    citation_reason: str
    state: str
    decided_by: str | None
    decided_at: datetime | None
    note: str | None

    model_config = {"from_attributes": True}


class IssueOut(BaseModel):
    id: uuid.UUID
    category: str
    rule: str
    severity: str
    title: str
    system: str | None
    spec_ref: dict[str, Any]
    drawing_ref: dict[str, Any]
    detail: dict[str, Any]
    state: str
    decided_by: str | None
    decided_at: datetime | None
    note: str | None

    model_config = {"from_attributes": True}


class ScopeRowOut(BaseModel):
    id: uuid.UUID
    system: str
    kind: str
    key: str
    label: str
    status: str
    proposed_status: str
    document_revision_id: uuid.UUID | None
    clause_id: uuid.UUID | None
    clause_number: str | None
    quote: str | None
    reason: str
    source: str
    note: str | None
    edited_by: str | None
    confirmed_by: str | None
    confirmed_at: datetime | None

    model_config = {"from_attributes": True}


class AnalysisOut(BaseModel):
    obligations: int
    issues_open: int
    issues_new: int
    issues_resolved: int
    rows: int


class DecisionIn(BaseModel):
    decision: str = Field(min_length=1, max_length=16)
    note: str | None = Field(default=None, max_length=2000)


class RowIn(BaseModel):
    status: str = Field(pattern="^(included|excluded|by_others|unclear)$")
    note: str | None = Field(default=None, max_length=2000)


def _refused(error: Exception) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error))


@router.post("/analysis/run", response_model=AnalysisOut)
def run(
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.DOCUMENT_REVIEW)],
) -> AnalysisOut:
    """Read the obligations, cross-check the drawings and build the matrix again. Nothing a
    person has decided is undone."""
    found = analysis.analyse(session, context.bid.id)
    return AnalysisOut(**found.__dict__)


@router.get("/obligations", response_model=list[ObligationOut])
def obligations(context: CurrentBid, session: DbSession) -> list[ObligationOut]:
    return [
        ObligationOut.model_validate(row)
        for row in analysis.list_obligations(session, context.bid.id)
    ]


@router.post("/obligations/{obligation_id}/decide", response_model=ObligationOut)
def decide_obligation(
    obligation_id: uuid.UUID,
    body: DecisionIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.DOCUMENT_REVIEW)],
) -> ObligationOut:
    row = session.get(SpecObligation, obligation_id)
    if row is None or row.bid_id != context.bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "obligation not found")
    try:
        analysis.decide_obligation(session, row, body.decision, principal.actor(), body.note)
    except analysis.AnalysisError as refusal:
        raise _refused(refusal) from refusal
    return ObligationOut.model_validate(row)


@router.post("/obligations/read-with-model", response_model=list[ObligationOut])
def read_with_model(
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.DOCUMENT_REVIEW)],
) -> list[ObligationOut]:
    """Ask the model about clauses that oblige something the rules put in no category. Its
    answers are proposals, each checked against the clause it cites."""
    from firebid.ai_gateway import gateway

    stored = []
    for revision in analysis.current_specifications(session, context.bid.id):
        stored.extend(analysis.read_obligations_with_model(session, revision.id, gateway()))
    return [ObligationOut.model_validate(row) for row in stored]


@router.get("/issues", response_model=list[IssueOut])
def issues(context: CurrentBid, session: DbSession, state: str | None = None) -> list[IssueOut]:
    """Conflicts, missing items and ambiguities, most severe first, each citing both sides."""
    return [
        IssueOut.model_validate(row) for row in analysis.list_issues(session, context.bid.id, state)
    ]


@router.get("/clarification-candidates", response_model=list[IssueOut])
def clarification_candidates(context: CurrentBid, session: DbSession) -> list[IssueOut]:
    """The open issues: what the clarifications register (P2-06) starts from."""
    return [
        IssueOut.model_validate(row)
        for row in analysis.clarification_candidates(session, context.bid.id)
    ]


@router.post("/issues/{issue_id}/decide", response_model=IssueOut)
def decide_issue(
    issue_id: uuid.UUID,
    body: DecisionIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.DOCUMENT_REVIEW)],
) -> IssueOut:
    row = session.get(SpecIssue, issue_id)
    if row is None or row.bid_id != context.bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "issue not found")
    try:
        analysis.decide_issue(session, row, body.decision, principal.actor(), body.note)
    except analysis.AnalysisError as refusal:
        raise _refused(refusal) from refusal
    return IssueOut.model_validate(row)


@router.get("/scope-matrix", response_model=list[ScopeRowOut])
def scope_matrix(context: CurrentBid, session: DbSession) -> list[ScopeRowOut]:
    return [ScopeRowOut.model_validate(row) for row in analysis.matrix(session, context.bid.id)]


@router.post("/scope-matrix/rows/{row_id}", response_model=ScopeRowOut)
def edit_row(
    row_id: uuid.UUID,
    body: RowIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.DOCUMENT_REVIEW)],
) -> ScopeRowOut:
    row = session.get(ScopeRow, row_id)
    if row is None or row.bid_id != context.bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    analysis.edit_row(session, row, body.status, principal.actor(), body.note)
    return ScopeRowOut.model_validate(row)


@router.post("/scope-matrix/confirm", response_model=list[ScopeRowOut])
def confirm_matrix(
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.DOCUMENT_REVIEW)],
) -> list[ScopeRowOut]:
    try:
        rows = analysis.confirm_matrix(session, context.bid.id, principal.actor())
    except analysis.AnalysisError as refusal:
        raise _refused(refusal) from refusal
    return [ScopeRowOut.model_validate(row) for row in rows]


@router.get("/scope-matrix/export.xlsx")
def export_matrix(context: CurrentBid, session: DbSession) -> Response:
    """The matrix as a workbook. A download is a named person's explicit action."""
    payload = analysis.export_matrix(session, context.bid)
    name = f"{context.bid.human_id}-scope-matrix.xlsx"
    return Response(
        content=payload,
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
