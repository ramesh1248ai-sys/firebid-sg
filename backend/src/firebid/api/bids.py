"""The bid workspace (FR-BID-01, FR-BID-02).

Every route here is scoped: listing shows only the caller's bids, and a bid-owned route uses
`CurrentBid`, which returns 404 to anyone who is not a member.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from firebid.api.deps import CurrentBid, CurrentPrincipal, DbSession, require
from firebid.auth.permissions import Action
from firebid.auth.provisioning import Principal
from firebid.db.audit import record_event
from firebid.db.models.core import AppUser, Bid, BidMember, Project, UserRole
from firebid.db.models.workflow import HumanTask
from firebid.domain.actors import AuditContext
from firebid.domain.state_machines import BidState, Role, TransitionError
from firebid.services.bids import NewBid, create_bid, dashboard, missing_mandatory_fields
from firebid.services.transitions import apply_transition, bid_moves

router = APIRouter(prefix="/bids", tags=["bids"])


class BidCreate(BaseModel):
    project_name: str = Field(min_length=1, max_length=200)
    client_name: str = Field(min_length=1, max_length=200)
    tender_reference: str = Field(min_length=1, max_length=120)
    submission_deadline: datetime
    clarification_cutoff: datetime | None = None
    tender_validity_days: int | None = Field(default=None, ge=1, le=365)
    project_id: uuid.UUID | None = None
    consultant: str | None = Field(default=None, max_length=200)


class BidUpdate(BaseModel):
    client_name: str | None = Field(default=None, min_length=1, max_length=200)
    tender_reference: str | None = Field(default=None, min_length=1, max_length=120)
    submission_deadline: datetime | None = None
    clarification_cutoff: datetime | None = None
    tender_validity_days: int | None = Field(default=None, ge=1, le=365)
    stage: str | None = Field(default=None, pattern=r"^S[0-9]$")
    # The project's consultant: shared by every bid on the project.
    consultant: str | None = Field(default=None, max_length=200)


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
    consultant: str | None = None
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


class MemberRoleIn(BaseModel):
    role: Role


class MemberOut(BaseModel):
    user_id: uuid.UUID
    role: str
    display_name: str


class TeamCandidateOut(BaseModel):
    """Someone of the organisation who is not on the bid's team yet."""

    user_id: uuid.UUID
    display_name: str
    username: str
    roles: list[str]


class MoveOut(BaseModel):
    target: str
    action: str
    roles: list[str]
    permitted: bool
    refusal: str | None


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
    changes = body.model_dump(exclude_unset=True)
    if "consultant" in changes:
        consultant = (changes.pop("consultant") or "").strip() or None
        project = session.get(Project, bid.project_id)
        if project is not None:
            project.consultant = consultant
    for field, value in changes.items():
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


@router.get("/{bid_id}/transitions", response_model=list[MoveOut])
def list_moves(context: CurrentBid, session: DbSession) -> list[MoveOut]:
    """The moves open from the bid's state, each with whose it is and what is in its way.
    G3, G4 and the outcome are not among them: they are made where they are recorded."""
    return [
        MoveOut(
            target=move.target,
            action=move.action,
            roles=list(move.roles),
            permitted=move.permitted,
            refusal=move.refusal,
        )
        for move in bid_moves(session, context.bid, context.principal.actor())
    ]


@router.get("/{bid_id}/members/candidates", response_model=list[TeamCandidateOut])
def list_candidates(
    context: CurrentBid,
    session: DbSession,
    _: Annotated[Principal, require(Action.BID_MEMBER_MANAGE)],
) -> list[TeamCandidateOut]:
    """The organisation's people who are not on this bid, with the roles each holds: who a
    bid manager may add. Someone who has left (not active) is not offered, and nobody is
    offered twice."""
    on_the_bid = select(BidMember.user_id).where(BidMember.bid_id == context.bid.id)
    found = (
        session.execute(
            select(AppUser)
            .where(
                AppUser.organisation_id == context.bid.organisation_id,
                AppUser.is_active.is_(True),
                AppUser.id.not_in(on_the_bid),
            )
            .order_by(AppUser.created_at.desc())
        )
        .scalars()
        .all()
    )
    # A username is one person. Where the identity provider has issued them a new identity,
    # the row made at their latest first sign-in is the one they sign in as now.
    newest: dict[str, AppUser] = {}
    for person in found:
        newest.setdefault(person.username, person)
    people = sorted(newest.values(), key=lambda person: (person.display_name, person.username))
    held: dict[uuid.UUID, list[str]] = {}
    for user_id, role in session.execute(
        select(UserRole.user_id, UserRole.role).where(
            UserRole.user_id.in_([person.id for person in people])
        )
    ):
        held.setdefault(user_id, []).append(role)
    return [
        TeamCandidateOut(
            user_id=person.id,
            display_name=person.display_name,
            username=person.username,
            roles=sorted(held.get(person.id, [])),
        )
        for person in people
    ]


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
    # Someone of another organisation is not found, as someone who does not exist is not.
    if person is None or person.organisation_id != context.bid.organisation_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    existing = session.get(BidMember, (context.bid.id, body.user_id))
    if existing is not None:
        return _set_role(session, context, existing, person, str(body.role))
    session.add(BidMember(bid_id=context.bid.id, user_id=body.user_id, role=str(body.role)))
    session.flush()
    _team_event(session, context, "added", person, after={"role": str(body.role)})
    return MemberOut(user_id=person.id, role=str(body.role), display_name=person.display_name)


def _team_event(
    session: DbSession,
    context: CurrentBid,
    what: str,
    person: AppUser,
    *,
    before: dict[str, str] | None = None,
    after: dict[str, str] | None = None,
) -> None:
    record_event(
        session,
        context=AuditContext(organisation_id=context.bid.organisation_id, bid_id=context.bid.id),
        actor=context.principal.actor(),
        action=f"bid team: {what}",
        entity_type=BidMember.__tablename__,
        entity_id=person.id,
        before=before,
        after=after,
    )


def _keeps_a_manager(session: DbSession, bid_id: uuid.UUID, member: BidMember) -> None:
    """A bid keeps at least one bid manager: without one nobody could add to its team or
    move it on."""
    if member.role != str(Role.BID_MANAGER):
        return
    others = session.execute(
        select(func.count())
        .select_from(BidMember)
        .where(
            BidMember.bid_id == bid_id,
            BidMember.role == str(Role.BID_MANAGER),
            BidMember.user_id != member.user_id,
        )
    ).scalar_one()
    if not others:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "a bid keeps at least one bid manager: add another before changing this one",
        )


def _set_role(
    session: DbSession, context: CurrentBid, member: BidMember, person: AppUser, role: str
) -> MemberOut:
    if member.role != role:
        _keeps_a_manager(session, context.bid.id, member)
        before = member.role
        member.role = role
        session.flush()
        _team_event(
            session, context, "role changed", person, before={"role": before}, after={"role": role}
        )
    return MemberOut(user_id=person.id, role=role, display_name=person.display_name)


def _member(
    session: DbSession, context: CurrentBid, user_id: uuid.UUID
) -> tuple[BidMember, AppUser]:
    member = session.get(BidMember, (context.bid.id, user_id))
    person = session.get(AppUser, user_id)
    if member is None or person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "they are not on this bid")
    return member, person


@router.patch("/{bid_id}/members/{user_id}", response_model=MemberOut)
def change_member_role(
    user_id: uuid.UUID,
    body: MemberRoleIn,
    context: CurrentBid,
    session: DbSession,
    _: Annotated[Principal, require(Action.BID_MEMBER_MANAGE)],
) -> MemberOut:
    """The role someone holds on this bid. Their role in the organisation is not changed."""
    member, person = _member(session, context, user_id)
    return _set_role(session, context, member, person, str(body.role))


@router.delete("/{bid_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    user_id: uuid.UUID,
    context: CurrentBid,
    session: DbSession,
    _: Annotated[Principal, require(Action.BID_MEMBER_MANAGE)],
) -> Response:
    """Take someone off the bid: they no longer see it. What they did on it stays on record
    under their name."""
    member, person = _member(session, context, user_id)
    _keeps_a_manager(session, context.bid.id, member)
    role = member.role
    session.delete(member)
    session.flush()
    _team_event(session, context, "removed", person, before={"role": role})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    project = session.get(Project, bid.project_id)
    out.consultant = project.consultant if project else None
    out.missing_mandatory_fields = missing_mandatory_fields(session, bid)
    return out
