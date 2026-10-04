"""Bid risk and qualifications (FR-RSK-01 to 06; P2-07).

* `GET /bids/{id}/risk`: the scope checklist, the risk register, the qualifications list
  and G3 readiness, together.
* `POST /bids/{id}/risk/checklist/build`, `PUT .../checklist/{check}`.
* `POST /bids/{id}/risk/find`; `PUT .../risks/{risk}` (treatment, owner, status);
  `PUT .../risks/{risk}/impact`; `GET .../risks/{risk}/history`.
* `POST /bids/{id}/risk/qualifications/propose`, `POST .../qualifications` (a person's own),
  `PATCH .../qualifications/{q}`, `GET .../qualifications/{q}/history`.
* `GET /bids/{id}/risk/g3`.

Accepting or rejecting a qualification is `POST /bids/{id}/qualifications/{q}/decide`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from firebid.api.deps import CurrentBid, DbSession
from firebid.db.models.clarifications import QUALIFICATION_KINDS, Qualification
from firebid.db.models.core import Bid
from firebid.db.models.risk import Risk, ScopeCheck
from firebid.risk import rules
from firebid.services import risk as service

router = APIRouter(prefix="/bids/{bid_id}/risk", tags=["risk"])


def refused(error: Exception, code: int = status.HTTP_422_UNPROCESSABLE_CONTENT) -> HTTPException:
    return HTTPException(code, str(error))


class CheckOut(BaseModel):
    id: uuid.UUID
    system: str
    item_key: str
    label: str
    proposed_status: str
    status: str
    basis: str
    evidence: list[dict[str, Any]]
    decided_by: str | None
    decided_at: datetime | None
    note: str | None

    model_config = {"from_attributes": True}


class RiskOut(BaseModel):
    id: uuid.UUID
    key: str
    category: str
    kind: str
    title: str
    description: str
    evidence: list[dict[str, Any]]
    level: str | None
    proposed_treatment: str
    treatment: str | None
    owner: str | None
    status: str
    note: str | None
    impact: dict[str, Any]
    impact_state: str
    cost_allowance: Decimal | None
    programme_hours: Decimal | None
    impact_reason: str | None
    impact_by: str | None
    impact_at: datetime | None


class QualificationEntryOut(BaseModel):
    id: uuid.UUID
    kind: str
    text: str
    source_kind: str
    source_ref: str
    source_label: str
    state: str
    decided_by: str | None
    decided_at: datetime | None
    note: str | None

    model_config = {"from_attributes": True}


class ReadinessOut(BaseModel):
    ready: bool
    checklist_built: bool
    open_checks: list[dict[str, Any]]
    untreated_risks: list[dict[str, Any]]
    summary: str


class RiskPageOut(BaseModel):
    checklist: list[CheckOut]
    risks: list[RiskOut]
    qualifications: list[QualificationEntryOut]
    g3: ReadinessOut
    statuses: list[str]
    treatments: list[str]
    kinds: list[str]


class CheckIn(BaseModel):
    status: str = Field(pattern="^(included|excluded|by_others|clarified)$")
    note: str | None = Field(default=None, max_length=2000)


class TreatIn(BaseModel):
    treatment: str | None = Field(default=None, pattern="^(price|qualify|clarify|accept)$")
    owner: str | None = Field(default=None, max_length=200)
    status: str | None = Field(default=None, pattern="^(open|treated|closed)$")
    note: str | None = Field(default=None, max_length=2000)


class RiskImpactIn(BaseModel):
    # Accept what the engines computed, or give figures and the reason for them.
    accept_computed: bool = False
    cost: Decimal | None = Field(default=None, ge=0)
    hours: Decimal | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, max_length=2000)


class QualificationEntryIn(BaseModel):
    kind: str = Field(pattern="^(qualification|assumption|exclusion|deviation)$")
    text: str = Field(min_length=1, max_length=6000)
    source_kind: str = Field(max_length=24)
    source_ref: str = Field(min_length=1, max_length=200)


class QualificationEditIn(BaseModel):
    kind: str | None = Field(
        default=None, pattern="^(qualification|assumption|exclusion|deviation)$"
    )
    text: str | None = Field(default=None, min_length=1, max_length=6000)
    note: str | None = Field(default=None, max_length=2000)


class HistoryOut(BaseModel):
    at: datetime
    by: str
    action: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    reason: str | None


def risk_out(row: Risk) -> RiskOut:
    return RiskOut(
        id=row.id,
        key=row.key,
        category=row.category,
        kind=row.kind,
        title=row.title,
        description=row.description,
        evidence=list(row.evidence or []),
        level=row.level,
        proposed_treatment=row.proposed_treatment,
        treatment=row.treatment,
        owner=row.owner,
        status=row.status,
        note=row.note,
        impact=dict(row.impact or {}),
        impact_state=row.impact_state,
        cost_allowance=row.cost_allowance.amount if row.cost_allowance is not None else None,
        programme_hours=row.programme_hours,
        impact_reason=row.impact_reason,
        impact_by=row.impact_by,
        impact_at=row.impact_at,
    )


def readiness_out(session: DbSession, bid: Bid) -> ReadinessOut:
    found = service.g3_readiness(session, bid.id)
    return ReadinessOut(
        ready=found.ready,
        checklist_built=found.checklist_built,
        open_checks=found.open_checks,
        untreated_risks=found.untreated_risks,
        summary=found.describe(),
    )


def page_out(session: DbSession, bid: Bid) -> RiskPageOut:
    return RiskPageOut(
        checklist=[CheckOut.model_validate(row) for row in service.checks(session, bid.id)],
        risks=[risk_out(row) for row in service.risks(session, bid.id)],
        qualifications=[
            QualificationEntryOut.model_validate(row)
            for row in service.qualifications(session, bid.id)
        ],
        g3=readiness_out(session, bid),
        statuses=list(rules.RESOLVED),
        treatments=list(rules.TREATMENTS),
        kinds=list(QUALIFICATION_KINDS),
    )


def _history(session: DbSession, entity_type: str, entity_id: uuid.UUID) -> list[HistoryOut]:
    return [
        HistoryOut(
            at=event.occurred_at,
            by=event.actor_label,
            action=event.action,
            before=dict(event.before) if event.before else None,
            after=dict(event.after) if event.after else None,
            reason=event.reason,
        )
        for event in service.history(session, entity_type, entity_id)
    ]


@router.get("", response_model=RiskPageOut)
def get_page(context: CurrentBid, session: DbSession) -> RiskPageOut:
    """The scope checklist, the risk register, the qualifications and G3 readiness."""
    return page_out(session, context.bid)


@router.get("/g3", response_model=ReadinessOut)
def g3(context: CurrentBid, session: DbSession) -> ReadinessOut:
    """What the Commercial Director sees before G3: whether every checklist item is
    resolved and every risk has a treatment, and which are not."""
    return readiness_out(session, context.bid)


@router.post("/checklist/build", response_model=RiskPageOut)
def build_checklist(context: CurrentBid, session: DbSession) -> RiskPageOut:
    """Build the checklist from the scope matrix and the takeoff, or refresh its proposals."""
    try:
        service.build_checklist(session, context.bid, context.principal.actor())
    except service.RiskError as refusal:
        raise refused(refusal, status.HTTP_409_CONFLICT) from refusal
    return page_out(session, context.bid)


@router.put("/checklist/{check_id}", response_model=RiskPageOut)
def resolve_check(
    check_id: uuid.UUID, body: CheckIn, context: CurrentBid, session: DbSession
) -> RiskPageOut:
    row = session.get(ScopeCheck, check_id)
    if row is None or row.bid_id != context.bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such checklist item")
    try:
        service.resolve_check(
            session, context.bid, row, context.principal.actor(), status=body.status, note=body.note
        )
    except service.RiskError as refusal:
        raise refused(refusal) from refusal
    return page_out(session, context.bid)


@router.post("/find", response_model=RiskPageOut)
def find(context: CurrentBid, session: DbSession) -> RiskPageOut:
    """Find the bid's design-responsibility and execution risks again, with their evidence
    and what each could cost."""
    service.find(session, context.bid, context.principal.actor())
    return page_out(session, context.bid)


def _risk(session: DbSession, bid: Bid, risk_id: uuid.UUID) -> Risk:
    row = session.get(Risk, risk_id)
    if row is None or row.bid_id != bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such risk")
    return row


@router.put("/risks/{risk_id}", response_model=RiskPageOut)
def treat(
    risk_id: uuid.UUID, body: TreatIn, context: CurrentBid, session: DbSession
) -> RiskPageOut:
    """Set a risk's treatment, owner or status."""
    row = _risk(session, context.bid, risk_id)
    try:
        service.treat(
            session,
            context.bid,
            row,
            context.principal.actor(),
            treatment=body.treatment,
            owner=body.owner,
            status=body.status,
            note=body.note,
        )
    except service.RiskError as refusal:
        raise refused(refusal) from refusal
    return page_out(session, context.bid)


@router.put("/risks/{risk_id}/impact", response_model=RiskPageOut)
def decide_impact(
    risk_id: uuid.UUID, body: RiskImpactIn, context: CurrentBid, session: DbSession
) -> RiskPageOut:
    """Accept the computed impact, or adjust it with a reason."""
    row = _risk(session, context.bid, risk_id)
    try:
        service.decide_impact(
            session,
            context.bid,
            row,
            context.principal.actor(),
            cost=body.cost,
            hours=body.hours,
            reason=body.reason,
            accept_computed=body.accept_computed,
        )
    except service.RiskError as refusal:
        raise refused(refusal) from refusal
    return page_out(session, context.bid)


@router.get("/risks/{risk_id}/history", response_model=list[HistoryOut])
def risk_history(risk_id: uuid.UUID, context: CurrentBid, session: DbSession) -> list[HistoryOut]:
    row = _risk(session, context.bid, risk_id)
    return _history(session, Risk.__tablename__, row.id)


@router.post("/qualifications/propose", response_model=RiskPageOut)
def propose_qualifications(context: CurrentBid, session: DbSession) -> RiskPageOut:
    """Propose assumptions, exclusions and qualifications from the risks, the checklist, the
    scope matrix and the measurement conventions, each linked to its source."""
    service.propose_qualifications(session, context.bid, context.principal.actor())
    return page_out(session, context.bid)


@router.post("/qualifications", response_model=RiskPageOut, status_code=201)
def add_qualification(
    body: QualificationEntryIn, context: CurrentBid, session: DbSession
) -> RiskPageOut:
    """An entry a person writes, linked to the item it comes from."""
    try:
        service.add_qualification(
            session,
            context.bid,
            context.principal.actor(),
            kind=body.kind,
            text=body.text,
            source_kind=body.source_kind,
            source_ref=body.source_ref,
        )
    except service.RiskError as refusal:
        raise refused(refusal) from refusal
    return page_out(session, context.bid)


def _entry(session: DbSession, bid: Bid, entry_id: uuid.UUID) -> Qualification:
    row = session.get(Qualification, entry_id)
    if row is None or row.bid_id != bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such entry")
    return row


@router.patch("/qualifications/{entry_id}", response_model=RiskPageOut)
def edit_qualification(
    entry_id: uuid.UUID, body: QualificationEditIn, context: CurrentBid, session: DbSession
) -> RiskPageOut:
    row = _entry(session, context.bid, entry_id)
    try:
        service.edit_qualification(
            session,
            context.bid,
            row,
            context.principal.actor(),
            text=body.text,
            kind=body.kind,
            note=body.note,
        )
    except service.RiskError as refusal:
        raise refused(refusal) from refusal
    return page_out(session, context.bid)


@router.get("/qualifications/{entry_id}/history", response_model=list[HistoryOut])
def qualification_history(
    entry_id: uuid.UUID, context: CurrentBid, session: DbSession
) -> list[HistoryOut]:
    row = _entry(session, context.bid, entry_id)
    return _history(session, Qualification.__tablename__, row.id)
