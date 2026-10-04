"""The review pack, gates G3 and G4, the frozen submission, outcomes and library proposals
(FR-PKG-01, 02, 03; FR-LRN-02, 03; P2-08).

* `GET  /bids/{id}/review-pack` (its sections), `GET .../review-pack/download?format=`.
* `GET  /bids/{id}/gates`; `POST .../gates/g3/approve`, `.../gates/g4/approve`.
* `GET  /bids/{id}/submission` (the snapshot, verified), `GET .../submission/files/{name}`.
* `GET`, `POST /bids/{id}/outcome`; `GET /outcomes` (the organisation's report).
* `GET`, `POST /library-proposals`; `POST /library-proposals/{id}/decide`.

G1 and G2 are approved where their work is done (`/qto/g1/approve`, `/boq/g2/approve`).
Nothing here sends a bid anywhere: after G4 the files are offered for download to a person
on the bid, and that is the only way out.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from firebid.api.deps import CurrentBid, CurrentPrincipal, DbSession, require
from firebid.auth.permissions import Action
from firebid.auth.provisioning import Principal
from firebid.db.models.submission import BidOutcome, LibraryProposal
from firebid.services import library_governance as governance
from firebid.services import review_pack, submission
from firebid.storage.object_store import get_snapshot_store

router = APIRouter(prefix="/bids/{bid_id}", tags=["submission"])
company_router = APIRouter(tags=["submission"])

FORMATS = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


def refused(error: Exception, code: int = status.HTTP_409_CONFLICT) -> HTTPException:
    return HTTPException(code, str(error))


# --- The review pack --------------------------------------------------------------------------


class PackSectionOut(BaseModel):
    key: str
    title: str
    link: str
    columns: list[str]
    rows: list[list[str]]
    note: str


class PackOut(BaseModel):
    human_id: str
    client: str
    reference: str
    generated_at: datetime
    priced_on: date
    figures: dict[str, str]
    figure_labels: dict[str, str]
    sections: list[PackSectionOut]


@router.get("/review-pack", response_model=PackOut)
def get_pack(context: CurrentBid, session: DbSession) -> PackOut:
    """The review pack for G2 and G3: the estimate by component and by system, cost drivers
    and margin, risk allowances, variances, what is open or unpriced, and G1 coverage."""
    pack = review_pack.build(session, context.bid)
    return PackOut(
        human_id=pack.human_id,
        client=pack.client,
        reference=pack.reference,
        generated_at=pack.generated_at,
        priced_on=pack.priced_on,
        figures=pack.figures,
        figure_labels=dict(review_pack.FIGURE_LABELS),
        sections=[PackSectionOut(**section.as_json()) for section in pack.sections],
    )


@router.get("/review-pack/download")
def download_pack(context: CurrentBid, session: DbSession, format: str = "pdf") -> Response:
    """The review pack as a PDF or a workbook, for the person asking."""
    if format not in FORMATS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "a review pack is a pdf or xlsx")
    pack = review_pack.build(session, context.bid)
    content = review_pack.as_pdf(pack) if format == "pdf" else review_pack.as_workbook(pack)
    name = f"{context.bid.human_id} review pack.{format}"
    return Response(
        content,
        media_type=FORMATS[format],
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# --- Gates ------------------------------------------------------------------------------------


class GateOut(BaseModel):
    gate: str
    role: str
    approved: bool
    blockers: list[str]
    approver_role: str | None
    approver_id: uuid.UUID | None
    decided_at: datetime | None
    comment: str | None
    snapshot_hash: str | None


class GatesOut(BaseModel):
    state: str
    gates: list[GateOut]


class GateApproveIn(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


def gates_out(session: DbSession, context: Any) -> GatesOut:
    return GatesOut(
        state=context.bid.state,
        gates=[GateOut(**item.__dict__) for item in submission.gate_status(session, context.bid)],
    )


@router.get("/gates", response_model=GatesOut)
def gates(context: CurrentBid, session: DbSession) -> GatesOut:
    """Each gate: approved, by whom, when and on which hash; or what stands in its way."""
    return gates_out(session, context)


@router.post("/gates/g3/approve", response_model=GatesOut, status_code=201)
def approve_g3(
    body: GateApproveIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.GATE_G3_APPROVE)],
) -> GatesOut:
    """The Commercial Director approves the estimate. Refused, with the reasons, while G2 is
    not approved, a scope checklist item is open or a risk has no treatment."""
    try:
        submission.approve_g3(session, context.bid, principal.actor(), body.comment)
    except submission.GateError as refusal:
        raise refused(refusal) from refusal
    return gates_out(session, context)


@router.post("/gates/g4/approve", response_model=GatesOut, status_code=201)
def approve_g4(
    body: GateApproveIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.GATE_G4_APPROVE)],
) -> GatesOut:
    """The Commercial Director approves submission: the submission is frozen and the bid
    becomes submitted. The platform sends nothing; the files are then offered for download."""
    try:
        submission.approve_g4(
            session, context.bid, principal.actor(), get_snapshot_store(), body.comment
        )
    except submission.GateError as refusal:
        raise refused(refusal) from refusal
    return gates_out(session, context)


# --- The frozen submission --------------------------------------------------------------------


class SnapshotFileOut(BaseModel):
    name: str
    sha256: str
    size: int
    content_type: str


class SnapshotOut(BaseModel):
    id: uuid.UUID
    manifest_sha256: str
    frozen_by: str
    frozen_at: datetime
    files: list[SnapshotFileOut]
    record_counts: dict[str, int]
    verified: bool
    problems: list[str]
    changed_since: list[str]


@router.get("/submission", response_model=SnapshotOut | None)
def get_submission(context: CurrentBid, session: DbSession) -> SnapshotOut | None:
    """The frozen submission and whether it still verifies: the manifest and every file are
    read back and their hashes checked."""
    snapshot = submission.snapshot_of(session, context.bid.id)
    if snapshot is None:
        return None
    found = submission.verify_snapshot(session, get_snapshot_store(), snapshot, context.bid)
    return SnapshotOut(
        id=snapshot.id,
        manifest_sha256=snapshot.manifest_sha256,
        frozen_by=snapshot.frozen_by,
        frozen_at=snapshot.created_at,
        files=[
            SnapshotFileOut(
                name=str(item["name"]),
                sha256=str(item["sha256"]),
                size=int(item["size"]),
                content_type=str(item["content_type"]),
            )
            for item in snapshot.files
        ],
        record_counts=dict(snapshot.record_counts),
        verified=found.ok,
        problems=found.problems,
        changed_since=found.changed_since,
    )


@router.get("/submission/files/{name}")
def submission_file(name: str, context: CurrentBid, session: DbSession) -> Response:
    """A file of the frozen submission, for the person asking. Before G4 there is none."""
    try:
        content, media = submission.submission_file(
            session, context.bid, get_snapshot_store(), name, context.principal.actor()
        )
    except submission.GateError as refusal:
        raise refused(refusal) from refusal
    return Response(
        content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{context.bid.human_id} {name}"'},
    )


# --- Outcomes ---------------------------------------------------------------------------------


class OutcomeIn(BaseModel):
    outcome: str = Field(pattern="^(awarded|lost|withdrawn)$")
    awarded_price: Decimal | None = Field(default=None, gt=0)
    reasons: str = Field(default="", max_length=6000)
    competitor_feedback: str = Field(default="", max_length=6000)


class OutcomeOut(BaseModel):
    outcome: str
    awarded_price: Decimal | None
    reasons: str
    competitor_feedback: str
    recorded_by: str
    recorded_at: datetime
    state: str


class OutcomeReportOut(BaseModel):
    submitted: int
    awarded: int
    lost: int
    withdrawn: int
    awaiting: int
    win_rate_percent: float | None
    awarded_value: Decimal
    rows: list[dict[str, Any]]


def outcome_out(row: BidOutcome, state: str) -> OutcomeOut:
    return OutcomeOut(
        outcome=row.outcome,
        awarded_price=row.awarded_price.amount if row.awarded_price is not None else None,
        reasons=row.reasons,
        competitor_feedback=row.competitor_feedback,
        recorded_by=row.recorded_by,
        recorded_at=row.recorded_at,
        state=state,
    )


@router.get("/outcome", response_model=OutcomeOut | None)
def get_outcome(context: CurrentBid, session: DbSession) -> OutcomeOut | None:
    row = submission.outcome_of(session, context.bid.id)
    return outcome_out(row, context.bid.state) if row is not None else None


@router.post("/outcome", response_model=OutcomeOut)
def record_outcome(body: OutcomeIn, context: CurrentBid, session: DbSession) -> OutcomeOut:
    """Record how the tender ended: awarded (with the price, where known), lost or withdrawn,
    with the reasons and what was learned of competitors. The bid's lifecycle follows."""
    try:
        row = submission.record_outcome(
            session,
            context.bid,
            context.principal.actor(),
            outcome=body.outcome,
            awarded_price=body.awarded_price,
            reasons=body.reasons,
            competitor_feedback=body.competitor_feedback,
        )
    except submission.GateError as refusal:
        raise refused(refusal) from refusal
    return outcome_out(row, context.bid.state)


@company_router.get("/outcomes", response_model=OutcomeReportOut)
def outcomes(principal: CurrentPrincipal, session: DbSession) -> OutcomeReportOut:
    """Tenders submitted, won, lost and still out, with the win rate and the value won."""
    found = submission.outcome_report(session, principal.organisation_id)
    return OutcomeReportOut(**found.__dict__)


# --- Library proposals ------------------------------------------------------------------------


class ProposalIn(BaseModel):
    library: str = Field(pattern="^(rate|productivity)$")
    payload: dict[str, Any]
    source: str = Field(pattern="^(quotation|outcome|estimator)$")
    source_ref: str | None = Field(default=None, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)


class ProposalDecisionIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=2000)


class ProposalOut(BaseModel):
    id: uuid.UUID
    library: str
    payload: dict[str, Any]
    source: str
    source_ref: str | None
    reason: str
    state: str
    proposed_by: str
    decided_by: str | None
    decided_at: datetime | None
    note: str | None
    applied_entry_id: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


@company_router.get("/library-proposals", response_model=list[ProposalOut])
def list_proposals(
    principal: CurrentPrincipal, session: DbSession, state: str | None = None
) -> list[ProposalOut]:
    """The queue of proposed changes to the rate and productivity libraries."""
    return [
        ProposalOut.model_validate(row)
        for row in governance.proposals(session, principal.organisation_id, state)
    ]


@company_router.post("/library-proposals", response_model=ProposalOut, status_code=201)
def propose(body: ProposalIn, principal: CurrentPrincipal, session: DbSession) -> ProposalOut:
    """Propose a change. The library is not changed until an estimator approves it."""
    try:
        row = governance.propose(
            session,
            principal.organisation_id,
            principal.actor(),
            library=body.library,
            payload=body.payload,
            source=body.source,
            reason=body.reason,
            source_ref=body.source_ref,
        )
    except governance.ProposalError as refusal:
        raise refused(refusal, status.HTTP_422_UNPROCESSABLE_CONTENT) from refusal
    return ProposalOut.model_validate(row)


@company_router.post("/library-proposals/{proposal_id}/decide", response_model=ProposalOut)
def decide_proposal(
    proposal_id: uuid.UUID,
    body: ProposalDecisionIn,
    principal: CurrentPrincipal,
    session: DbSession,
) -> ProposalOut:
    """Approve a proposed change, which applies it as a new version, or reject it."""
    row = session.get(LibraryProposal, proposal_id)
    if row is None or row.organisation_id != principal.organisation_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such proposal")
    try:
        governance.decide(session, row, principal.actor(), approve=body.approve, note=body.note)
    except governance.ProposalError as refusal:
        code = (
            status.HTTP_403_FORBIDDEN
            if "is approved by the" in str(refusal)
            else status.HTTP_409_CONFLICT
        )
        raise refused(refusal, code) from refusal
    return ProposalOut.model_validate(row)
