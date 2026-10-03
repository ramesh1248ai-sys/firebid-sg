"""Revisions, deltas, sampling and shared takeoffs (P2-02).

* `GET  /bids/{id}/sheets/{sheet}/revisions`, `.../revision-diff`: what a revision of a
  drawing changed from another (FR-DOC-08).
* `GET  /bids/{id}/qto/baselines`, `/qto/delta`: the takeoff against a baseline, and where
  G1 stands (FR-QTO-12).
* `GET  /coverage-policies`, `POST /coverage-policies/{category}`: how each item category is
  covered; `GET /bids/{id}/review/sampling`, `POST .../{category}/draw`, `.../accept`: the
  samples of a bid (FR-REV-05).
* `GET  /bids/{id}/shared-takeoff`, `POST .../publish`, `.../adopt`: one verified takeoff
  for a project's bids (FR-BID-04).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from firebid.api.deps import CurrentBid, CurrentPrincipal, DbSession, require
from firebid.auth.permissions import Action
from firebid.auth.provisioning import Principal
from firebid.domain.state_machines import TransitionError
from firebid.services import delta, revision_compare, sampling, shared_takeoff

router = APIRouter(prefix="/bids/{bid_id}", tags=["changes"])
policies_router = APIRouter(prefix="/coverage-policies", tags=["changes"])


def _refused(error: Exception) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(error))


# --- Revision comparison --------------------------------------------------------------------


class RevisionOut(BaseModel):
    sheet_id: uuid.UUID
    sheet_number: str | None
    revision: str | None
    state: str
    revision_date: Any | None


class ChangeOut(BaseModel):
    change: str
    kind: str
    object_type: str
    x: float
    y: float
    old_id: str | None
    new_id: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    points: list[list[float]]


class RevisionDiffOut(BaseModel):
    old: dict[str, Any]
    new: dict[str, Any]
    alignment: dict[str, Any]
    counts: dict[str, int]
    changes: list[ChangeOut]


@router.get("/sheets/{sheet_id}/revisions", response_model=list[RevisionOut])
def revisions(sheet_id: uuid.UUID, context: CurrentBid, session: DbSession) -> list[RevisionOut]:
    """Every revision of the drawing this sheet is one of, newest first."""
    return [
        RevisionOut(
            sheet_id=row.sheet_id,
            sheet_number=row.sheet_number,
            revision=row.revision_label,
            state=row.state,
            revision_date=row.revision_date,
        )
        for row in revision_compare.revisions(session, context.bid.id, sheet_id)
    ]


@router.get("/sheets/{sheet_id}/revision-diff", response_model=RevisionDiffOut)
def revision_diff(
    sheet_id: uuid.UUID,
    context: CurrentBid,
    session: DbSession,
    against: uuid.UUID | None = None,
) -> RevisionDiffOut:
    """What this sheet changed from another revision: `against`, or the one it superseded.

    Every change is located on this sheet, so the workbench draws it over the drawing.
    """
    try:
        found = revision_compare.compare(session, context.bid.id, sheet_id, against)
    except revision_compare.CompareError as refusal:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(refusal)) from refusal
    return RevisionDiffOut(
        old=found.old.as_json(),
        new=found.new.as_json(),
        alignment=found.diff.alignment.as_json(),
        counts=found.diff.counts(),
        changes=[ChangeOut(**change.as_json()) for change in found.diff.changes],
    )


# --- Delta QTO ------------------------------------------------------------------------------


class BaselineOut(BaseModel):
    id: uuid.UUID
    reason: str
    taken_at: datetime
    addendum_id: uuid.UUID | None
    items: int


class DeltaOut(BaseModel):
    baseline: BaselineOut
    counts: dict[str, int]
    items: list[dict[str, Any]]
    lines: list[dict[str, Any]]
    gate: dict[str, Any]


def _baseline(row: Any) -> BaselineOut:
    return BaselineOut(
        id=row.id,
        reason=row.reason,
        taken_at=row.created_at,
        addendum_id=row.addendum_id,
        items=len(row.items),
    )


@router.get("/qto/baselines", response_model=list[BaselineOut])
def baselines(context: CurrentBid, session: DbSession) -> list[BaselineOut]:
    return [_baseline(row) for row in delta.snapshots(session, context.bid.id)]


@router.get("/qto/delta", response_model=DeltaOut)
def delta_report(
    context: CurrentBid,
    session: DbSession,
    baseline: uuid.UUID | None = None,
    changed_only: bool = True,
) -> DeltaOut:
    """The takeoff now against a baseline (the latest unless one is named): what was added,
    removed and changed with its value before, per item and per BOQ line; and where G1
    stands, with the items it was reopened for."""
    try:
        snapshot, found = delta.report(session, context.bid.id, baseline)
    except delta.DeltaError as refusal:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(refusal)) from refusal
    items = found.changed() if changed_only else found.items
    return DeltaOut(
        baseline=_baseline(snapshot),
        counts=found.counts(),
        items=[item.as_json() for item in items],
        lines=[line.as_json() for line in found.lines],
        gate=delta.gate_status(session, context.bid.id),
    )


# --- Sampling -------------------------------------------------------------------------------


class PolicyIn(BaseModel):
    mode: str = Field(pattern="^(full|sampling)$")
    tolerable_error_percent: Annotated[float, Field(gt=0, lt=100)] = 5.0
    confidence_percent: Annotated[float, Field(ge=50, lt=100)] = 95.0
    accept_errors: Annotated[int, Field(ge=0, le=20)] = 0
    note: str | None = Field(default=None, max_length=2000)


class PolicyOut(BaseModel):
    category: str
    version: int
    mode: str
    tolerable_error_percent: float
    confidence_percent: float
    accept_errors: int
    note: str | None
    set_at: datetime


def _policy(row: Any) -> PolicyOut:
    return PolicyOut(
        category=row.category,
        version=row.version,
        mode=row.mode,
        tolerable_error_percent=float(row.tolerable_error_percent),
        confidence_percent=float(row.confidence_percent),
        accept_errors=row.accept_errors,
        note=row.note,
        set_at=row.created_at,
    )


@policies_router.get("", response_model=list[PolicyOut])
def list_policies(principal: CurrentPrincipal, session: DbSession) -> list[PolicyOut]:
    """The policies in force. A category not listed is in full review."""
    rows = sampling.policy_rows(session, principal.organisation_id)
    return [_policy(rows[category]) for category in sorted(rows)]


@policies_router.post("/{category}", response_model=PolicyOut)
def set_policy(
    category: str,
    body: PolicyIn,
    session: DbSession,
    principal: Annotated[Principal, require(Action.COVERAGE_POLICY_CHANGE)],
) -> PolicyOut:
    """A new version of the category's coverage policy, audited."""
    try:
        row = sampling.set_policy(
            session,
            principal.organisation_id,
            category,
            mode=body.mode,
            actor=principal.actor(),
            tolerable_error_percent=body.tolerable_error_percent,
            confidence_percent=body.confidence_percent,
            accept_errors=body.accept_errors,
            note=body.note,
        )
    except sampling.SamplingError as refusal:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(refusal)) from refusal
    return _policy(row)


@router.get("/review/sampling", response_model=list[dict[str, Any]])
def sampling_summary(context: CurrentBid, session: DbSession) -> list[dict[str, Any]]:
    """Each category on the bid: its policy, how its items came to be verified, its sample."""
    return sampling.summary(session, context.bid)


@router.post("/review/sampling/{category}/draw", response_model=dict[str, Any])
def draw_sample(
    category: str,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.QTO_EDIT)],
) -> dict[str, Any]:
    try:
        row = sampling.draw(session, context.bid, category, principal.actor())
    except sampling.SamplingError as refusal:
        raise _refused(refusal) from refusal
    return sampling.sample_json(row)


@router.post("/review/sampling/{category}/accept", response_model=dict[str, Any])
def accept_on_sample(
    category: str,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.GATE_G1_APPROVE)],
) -> dict[str, Any]:
    """Accept the category on its sample: the Senior Estimator's named action verifies the
    lot's remaining items. Refused while the sample is unfinished, or once it escalated."""
    try:
        row = sampling.accept_on_sample(session, context.bid, category, principal.actor())
    except (sampling.SamplingError, TransitionError) as refusal:
        raise _refused(refusal) from refusal
    return sampling.sample_json(row)


# --- Shared takeoff -------------------------------------------------------------------------


class PublishIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class AdoptIn(BaseModel):
    shared_takeoff_id: uuid.UUID | None = None


@router.get("/shared-takeoff", response_model=dict[str, Any])
def shared_status(context: CurrentBid, session: DbSession) -> dict[str, Any]:
    return shared_takeoff.status(session, context.bid)


@router.post("/shared-takeoff/publish", response_model=dict[str, Any])
def publish(
    body: PublishIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.GATE_G1_APPROVE)],
) -> dict[str, Any]:
    """Publish this bid's verified takeoff to its project, for its other bids to adopt."""
    try:
        row = shared_takeoff.publish(session, context.bid, principal.actor(), body.note)
    except shared_takeoff.SharedTakeoffError as refusal:
        raise _refused(refusal) from refusal
    return {"id": str(row.id), "version": row.version, "items": len(row.items)}


@router.post("/shared-takeoff/adopt", response_model=dict[str, Any])
def adopt(
    body: AdoptIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.GATE_G1_APPROVE)],
) -> dict[str, Any]:
    """Adopt the project's published takeoff into this bid, verified by this named action."""
    try:
        found = shared_takeoff.adopt(
            session, context.bid, principal.actor(), body.shared_takeoff_id
        )
    except (shared_takeoff.SharedTakeoffError, TransitionError) as refusal:
        raise _refused(refusal) from refusal
    return {
        "version": found.shared.version,
        "source_bid": found.shared.source_bid_human_id,
        "created": found.created,
        "updated": found.updated,
        "unchanged": found.unchanged,
        "kept": found.kept,
        "withdrawn": found.withdrawn,
    }
