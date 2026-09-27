"""A bid's specification attributes: each with its citation, and a person's decision on it
(FR-SPEC-01, FR-SPEC-05).

Every attribute links to its clause (`GET …/spec/clauses/{id}`), so the words it rests on
are one click away. Confirming, editing and rejecting need `document.review`. The takeoff
view (`GET …/spec/for`) is exactly what the QTO engine reads: verified values of Current
specifications only, "not specified" for anything else.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from firebid.api.deps import CurrentBid, DbSession, require
from firebid.auth.permissions import Action
from firebid.auth.provisioning import Principal
from firebid.db.models.documents import DocumentRevision
from firebid.db.models.specs import SpecAttribute, SpecClause
from firebid.services import specs as service

router = APIRouter(prefix="/bids/{bid_id}/spec", tags=["specification"])


class SpecAttributeOut(BaseModel):
    lineage_id: uuid.UUID
    version: int
    system: str
    attribute: str
    value: str
    dn_min: int | None
    dn_max: int | None
    condition: str | None
    state: str
    method: str
    confidence: float
    citation_ok: bool
    citation_reason: str
    clause_id: uuid.UUID | None
    clause_number: str
    quote: str
    document_id: uuid.UUID
    document_revision_id: uuid.UUID
    document_title: str | None
    revision_label: str | None
    model: str | None = None
    prompt_version: str | None = None
    verified_by: str | None


class ClauseOut(BaseModel):
    id: uuid.UUID
    number: str
    heading: str
    text: str
    anchor: dict[str, int]
    system: str
    document_id: uuid.UUID
    document_title: str | None
    revision_label: str | None


class DecisionIn(BaseModel):
    verdict: Literal["confirm", "edit", "reject"]
    value: str | None = Field(default=None, max_length=200)
    dn_min: int | None = Field(default=None, ge=0, le=1000)
    dn_max: int | None = Field(default=None, ge=0, le=1000)
    condition: str | None = Field(default=None, max_length=200)
    clause_number: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=2000)


class CitationOut(BaseModel):
    document_id: uuid.UUID
    document_revision_id: uuid.UUID
    title: str | None
    revision_label: str | None
    clause: str
    anchor: dict[str, int]
    quote: str


class AnswerOut(BaseModel):
    attribute: str
    value: str
    citations: list[CitationOut]


def _out(row: SpecAttribute, revisions: dict[uuid.UUID, DocumentRevision]) -> SpecAttributeOut:
    revision = revisions[row.document_revision_id]
    provenance: dict[str, Any] = dict(row.provenance or {})
    return SpecAttributeOut(
        lineage_id=row.lineage_id,
        version=row.version,
        system=row.system,
        attribute=row.attribute,
        value=row.value,
        dn_min=row.dn_min,
        dn_max=row.dn_max,
        condition=row.condition,
        state=row.state,
        method=row.method,
        confidence=row.confidence,
        citation_ok=row.citation_ok,
        citation_reason=row.citation_reason,
        clause_id=row.clause_id,
        clause_number=row.clause_number,
        quote=row.quote,
        document_id=revision.document_id,
        document_revision_id=revision.id,
        document_title=revision.title,
        revision_label=revision.revision_label,
        model=provenance.get("model"),
        prompt_version=provenance.get("prompt_version"),
        verified_by=row.verified_by,
    )


def _revisions(session: DbSession, bid_id: uuid.UUID) -> dict[uuid.UUID, DocumentRevision]:
    return {
        row.id: row
        for row in session.execute(
            select(DocumentRevision).where(DocumentRevision.bid_id == bid_id)
        ).scalars()
    }


@router.get("/attributes", response_model=list[SpecAttributeOut])
def list_attributes(context: CurrentBid, session: DbSession) -> list[SpecAttributeOut]:
    """Every attribute's latest version: flagged citations and doubtful ones first."""
    revisions = _revisions(session, context.bid.id)
    rows = service.current_attributes(session, context.bid.id)
    rows.sort(key=lambda r: (r.citation_ok, r.confidence, r.system, r.attribute, r.dn_min or 0))
    return [_out(row, revisions) for row in rows if row.document_revision_id in revisions]


@router.get("/clauses/{clause_id}", response_model=ClauseOut)
def get_clause(clause_id: uuid.UUID, context: CurrentBid, session: DbSession) -> ClauseOut:
    clause = session.get(SpecClause, clause_id)
    if clause is None or clause.bid_id != context.bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such clause")
    revision = session.get(DocumentRevision, clause.document_revision_id)
    if revision is None:  # the clause's foreign key makes this unreachable
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such clause")
    return ClauseOut(
        id=clause.id,
        number=clause.number,
        heading=clause.heading,
        text=clause.text,
        anchor=dict(clause.anchor),
        system=clause.system,
        document_id=revision.document_id,
        document_title=revision.title,
        revision_label=revision.revision_label,
    )


@router.post("/attributes/{lineage_id}/decide", response_model=SpecAttributeOut)
def decide(
    lineage_id: uuid.UUID,
    body: DecisionIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.DOCUMENT_REVIEW)],
) -> SpecAttributeOut:
    existing = service.current(session, lineage_id)
    if existing is None or existing.bid_id != context.bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such attribute")
    try:
        row = service.decide(
            session,
            lineage_id,
            principal.actor(),
            verdict=body.verdict,
            value=body.value,
            dn_min=body.dn_min,
            dn_max=body.dn_max,
            condition=body.condition,
            clause_number=body.clause_number,
            note=body.note,
        )
    except service.SpecError as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal
    return _out(row, _revisions(session, context.bid.id))


@router.get("/for", response_model=list[AnswerOut])
def attributes_for(
    context: CurrentBid,
    session: DbSession,
    system: Annotated[str, Query(max_length=24)],
    dn: Annotated[int | None, Query(ge=0, le=1000)] = None,
    condition: Annotated[str | None, Query(max_length=200)] = None,
) -> list[AnswerOut]:
    """What takeoff reads for one system and size: verified values or "not specified"."""
    found = service.attributes_for(session, context.bid.id, system, dn, condition=condition)
    return [
        AnswerOut(
            attribute=answer.attribute,
            value=answer.value,
            citations=[
                CitationOut(
                    document_id=c.document_id,
                    document_revision_id=c.document_revision_id,
                    title=c.title,
                    revision_label=c.revision_label,
                    clause=c.clause,
                    anchor=c.anchor,
                    quote=c.quote,
                )
                for c in answer.citations
            ],
        )
        for answer in found.values()
    ]
