"""The organisation's people: who holds which role, and whose access is switched off (FR-ADM-01).

The identity provider says who someone is; the roles they hold in this application are kept
here and changed here, by a system administrator (ADR-013). A role on a bid is another
matter and is set on the bid's team.

Nobody changes their own access, and the organisation keeps at least one system
administrator who can sign in: either would be a way to lock everyone out of this screen.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select

from firebid.api.deps import CurrentPrincipal, DbSession, require
from firebid.auth.permissions import Action
from firebid.auth.provisioning import Principal
from firebid.db.audit import record_event
from firebid.db.models.core import AppUser, BidMember, UserRole
from firebid.domain.actors import AuditContext
from firebid.domain.state_machines import Role

router = APIRouter(prefix="/admin/users", tags=["admin"])
# Who the caller is, for anyone signed in.
me_router = APIRouter(tags=["users"])

Manager = Annotated[Principal, require(Action.USER_MANAGE)]
# `system` is the platform acting on its own: no person holds it.
ASSIGNABLE = tuple(role for role in Role if role is not Role.SYSTEM)


class UserOut(BaseModel):
    user_id: uuid.UUID
    username: str
    display_name: str
    is_active: bool
    roles: list[str]
    # How many bids they are on the team of.
    bids: int
    first_signed_in_at: datetime


class MeOut(BaseModel):
    user_id: uuid.UUID
    username: str
    display_name: str
    roles: list[str]


class UserIn(BaseModel):
    """What to change; what is left out stays as it is."""

    roles: list[Role] | None = None
    is_active: bool | None = None


def _roles(session: DbSession, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    held: dict[uuid.UUID, list[str]] = {}
    for user_id, role in session.execute(
        select(UserRole.user_id, UserRole.role).where(UserRole.user_id.in_(user_ids))
    ):
        held.setdefault(user_id, []).append(role)
    return held


def _out(person: AppUser, roles: list[str], bids: int) -> UserOut:
    return UserOut(
        user_id=person.id,
        username=person.username,
        display_name=person.display_name,
        is_active=person.is_active,
        roles=sorted(roles),
        bids=bids,
        first_signed_in_at=person.created_at,
    )


@me_router.get("/me", response_model=MeOut)
def me(principal: CurrentPrincipal) -> MeOut:
    """The caller as this application knows them. The roles are the ones held here, which
    the token's may no longer be: the screens show and hide by these."""
    return MeOut(
        user_id=principal.user_id,
        username=principal.username,
        display_name=principal.display_name,
        roles=sorted(principal.roles),
    )


@router.get("/roles", response_model=list[str])
def assignable_roles(_: Manager) -> list[str]:
    """The roles a person may be given, in the order of requirements §3."""
    return [str(role) for role in ASSIGNABLE]


@router.get("", response_model=list[UserOut])
def list_users(principal: Manager, session: DbSession) -> list[UserOut]:
    """Everyone who has signed in to this organisation, by name."""
    people = (
        session.execute(
            select(AppUser)
            .where(AppUser.organisation_id == principal.organisation_id)
            .order_by(AppUser.display_name, AppUser.username, AppUser.created_at)
        )
        .scalars()
        .all()
    )
    ids = [person.id for person in people]
    held = _roles(session, ids)
    on: dict[uuid.UUID, int] = {
        user_id: count
        for user_id, count in session.execute(
            select(BidMember.user_id, func.count())
            .where(BidMember.user_id.in_(ids))
            .group_by(BidMember.user_id)
        )
    }
    return [_out(person, held.get(person.id, []), on.get(person.id, 0)) for person in people]


def _keeps_an_admin(session: DbSession, organisation_id: uuid.UUID, but_for: uuid.UUID) -> None:
    others = session.execute(
        select(func.count())
        .select_from(AppUser)
        .join(UserRole, UserRole.user_id == AppUser.id)
        .where(
            AppUser.organisation_id == organisation_id,
            AppUser.is_active.is_(True),
            AppUser.id != but_for,
            UserRole.role == str(Role.SYSTEM_ADMIN),
        )
    ).scalar_one()
    if not others:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "the organisation keeps at least one system administrator who can sign in: "
            "give the role to someone else first",
        )


@router.patch("/{user_id}", response_model=UserOut)
def change_user(
    user_id: uuid.UUID, body: UserIn, principal: Manager, session: DbSession
) -> UserOut:
    """Set the roles someone holds, or switch their access off or on. It holds from their
    next request. What they did stays on record under their name, and the bids they are on
    keep them on the team."""
    person = session.get(AppUser, user_id, with_for_update=True)
    if person is None or person.organisation_id != principal.organisation_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "nobody of that id")
    before_roles = sorted(_roles(session, [person.id]).get(person.id, []))
    roles = sorted({str(role) for role in body.roles}) if body.roles is not None else before_roles
    active = person.is_active if body.is_active is None else body.is_active
    if str(Role.SYSTEM) in roles:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "'system' is the platform's own role"
        )
    if (roles, active) == (before_roles, person.is_active):
        return _out(person, before_roles, _bids(session, person.id))
    if person.id == principal.user_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "nobody changes their own access: ask another system administrator",
        )
    admin = str(Role.SYSTEM_ADMIN)
    was_admin = admin in before_roles and person.is_active
    if was_admin and not (admin in roles and active):
        _keeps_an_admin(session, person.organisation_id, person.id)

    if roles != before_roles:
        for role in set(before_roles) - set(roles):
            row = session.get(UserRole, (person.id, role))
            if row is not None:
                session.delete(row)
        for role in set(roles) - set(before_roles):
            session.add(UserRole(user_id=person.id, role=role))
        _event(
            session, principal, person, "roles changed", {"roles": before_roles}, {"roles": roles}
        )
    if active != person.is_active:
        person.is_active = active
        _event(
            session,
            principal,
            person,
            "access switched on" if active else "access switched off",
            {"is_active": not active},
            {"is_active": active},
        )
    session.flush()
    return _out(person, roles, _bids(session, person.id))


def _bids(session: DbSession, user_id: uuid.UUID) -> int:
    return int(
        session.execute(
            select(func.count()).select_from(BidMember).where(BidMember.user_id == user_id)
        ).scalar_one()
    )


def _event(
    session: DbSession,
    principal: Principal,
    person: AppUser,
    what: str,
    before: dict[str, object],
    after: dict[str, object],
) -> None:
    # The person is named by their id: a name is personal data the audit log need not copy.
    record_event(
        session,
        context=AuditContext(organisation_id=person.organisation_id),
        actor=principal.actor(),
        action=f"user: {what}",
        entity_type=AppUser.__tablename__,
        entity_id=person.id,
        before=before,
        after=after,
    )
