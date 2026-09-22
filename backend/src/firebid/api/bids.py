"""The bid workspace (FR-BID-01, FR-BID-02).

Every route here is scoped: listing shows only the caller's bids, and a bid-owned route uses
`CurrentBid`, which returns 404 to anyone who is not a member.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from firebid.api.deps import CurrentBid, CurrentPrincipal, DbSession, require
from firebid.auth.permissions import Action
from firebid.auth.provisioning import Principal
from firebid.db.models.core import AppUser, Bid, BidMember
from firebid.db.models.workflow import HumanTask
from firebid.domain.state_machines import BidState, Role, TransitionError
from firebid.services.bids import NewBid, create_bid, dashboard, missing_mandatory_fields
from firebid.services.transitions import apply_transition

router = APIRouter(prefix="/bids", tags=["bids"])


class BidCreate(BaseModel):
    project_name: str = Field(min_length=1, max_length=200)
    client_name: str = Field(min_length=1, max_length=200)
    tender_reference: str = Field(min_length=1, max_length=120)
    submission_deadline: datetime
    clarification_cutoff: datetime | None = None
    tender_validity_days: int | None = Field(default=None, ge=1, le=365)
    project_id: uuid.UUID | None = None


class BidUpdate(BaseModel):
    client_name: str | None = Field(default=None, min_length=1, max_length=200)
    tender_reference: str | None = Field(default=None, min_length=1, max_length=120)
    submission_deadline: datetime | None = None
    clarification_cutoff: datetime | None = None
    tender_validity_days: int | None = Field(default=None, ge=1, le=365)
    stage: str | None = Field(default=None, pattern=r"^S[0-9]$")


class BidOut(BaseModel):
    id: uuid.UUID
    human_id: str
    client_name: str
    tender_reference: str
    state: str
    stage: str
    submission_deadline: datetime
    clarification_cutoff: datetime | None
    tender_validity_days: int | None
    missing_mandatory_fields: list[str] = []

    model_config = {"from_attributes": True}


class BidSummaryOut(BidOut):
    open_tasks: int
    overdue_tasks: int
    gates_passed: list[str]
    days_to_submission: int | None
    days_to_clarification_cutoff: int | None


class MemberIn(BaseModel):
    user_id: uuid.UUID
    role: Role


class MemberOut(BaseModel):
    user_id: uuid.UUID
    role: str
    display_name: str


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    stage: str = Field(pattern=r"^S[0-9]$")
    assignee_id: uuid.UUID | None = None
    required_role: Role | None = None
    due_at: datetime | None = None


class TaskOut(BaseModel):
    id: uuid.UUID
    title: str
    state: str
    stage: str | None
    assignee_id: uuid.UUID | None
    due_at: datetime | None

    model_config = {"from_attributes": True}


class TransitionIn(BaseModel):
    target: BidState
    reason: str | None = Field(default=None, max_length=500)


@router.post("", response_model=BidOut, status_code=status.HTTP_201_CREATED)
def create(
    body: BidCreate,
    session: DbSession,
    principal: Annotated[Principal, require(Action.BID_CREATE)],
) -> BidOut:
    try:
        bid = create_bid(session, principal, NewBid(**body.model_dump()))
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    return _as_out(session, bid)


@router.get("", response_model=list[BidSummaryOut])
def list_bids(session: DbSession, principal: CurrentPrincipal) -> list[BidSummaryOut]:
    """The dashboard: the caller's bids with stage, gates, tasks and days to each deadline."""
    return [
        BidSummaryOut(
            **_as_out(session, summary.bid).model_dump(),
            open_tasks=summary.open_tasks,
            overdue_tasks=summary.overdue_tasks,
            gates_passed=summary.gates_passed,
            days_to_submission=summary.days_to_submission,
            days_to_clarification_cutoff=summary.days_to_clarification_cutoff,
        )
        for summary in dashboard(session, principal)
    ]


@router.get("/{bid_id}", response_model=BidOut)
def get_bid(context: CurrentBid, session: DbSession) -> BidOut:
    return _as_out(session, context.bid)


@router.patch("/{bid_id}", response_model=BidOut)
def update_bid(body: BidUpdate, context: CurrentBid, session: DbSession) -> BidOut:
    bid = context.bid
    if bid.state == str(BidState.SUBMITTED):
        raise HTTPException(status.HTTP_409_CONFLICT, "a submitted bid is frozen")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(bid, field, value)
    session.flush()
    return _as_out(session, bid)


@router.post("/{bid_id}/transitions", response_model=BidOut)
def transition(body: TransitionIn, context: CurrentBid, session: DbSession) -> BidOut:
    try:
        apply_transition(
            session,
            context.bid,
            target=body.target,
            actor=context.principal.actor(),
            reason=body.reason,
        )
    except TransitionError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return _as_out(session, context.bid)


@router.get("/{bid_id}/members", response_model=list[MemberOut])
def list_members(context: CurrentBid, session: DbSession) -> list[MemberOut]:
    rows = session.execute(
        select(BidMember, AppUser)
        .join(AppUser, AppUser.id == BidMember.user_id)
        .where(BidMember.bid_id == context.bid.id)
    ).all()
    return [
        MemberOut(user_id=member.user_id, role=member.role, display_name=person.display_name)
        for member, person in rows
    ]


@router.post("/{bid_id}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def add_member(
    body: MemberIn,
    context: CurrentBid,
    session: DbSession,
    _: Annotated[Principal, require(Action.BID_MEMBER_MANAGE)],
) -> MemberOut:
    person = session.get(AppUser, body.user_id)
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    session.merge(BidMember(bid_id=context.bid.id, user_id=body.user_id, role=str(body.role)))
    session.flush()
    return MemberOut(user_id=person.id, role=str(body.role), display_name=person.display_name)


@router.get("/{bid_id}/tasks", response_model=list[TaskOut])
def list_tasks(context: CurrentBid, session: DbSession) -> list[TaskOut]:
    tasks = (
        session.execute(
            select(HumanTask)
            .where(HumanTask.bid_id == context.bid.id)
            .order_by(HumanTask.due_at.nulls_last(), HumanTask.created_at)
        )
        .scalars()
        .all()
    )
    return [_task_out(task) for task in tasks]


@router.post("/{bid_id}/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def add_task(body: TaskIn, context: CurrentBid, session: DbSession) -> TaskOut:
    task = HumanTask(
        bid_id=context.bid.id,
        kind="bid_task",
        title=body.title,
        assignee_id=body.assignee_id,
        required_role=str(body.required_role) if body.required_role else None,
        due_at=body.due_at,
        payload={"stage": body.stage},
    )
    session.add(task)
    session.flush()
    return _task_out(task)


def _task_out(task: HumanTask) -> TaskOut:
    stage = task.payload.get("stage") if isinstance(task.payload, dict) else None
    return TaskOut(
        id=task.id,
        title=task.title,
        state=task.state,
        stage=str(stage) if stage else None,
        assignee_id=task.assignee_id,
        due_at=task.due_at,
    )


def _as_out(session: DbSession, bid: Bid) -> BidOut:
    out = BidOut.model_validate(bid)
    out.missing_mandatory_fields = missing_mandatory_fields(session, bid)
    return out
