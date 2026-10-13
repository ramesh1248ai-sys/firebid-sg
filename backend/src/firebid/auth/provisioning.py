"""Turning a verified identity into a user of this installation.

The identity provider owns identity; this application owns roles and bid membership. A person
is created on first sign-in with the application roles their token carries. After that their
name is refreshed on each sign-in and their roles are the ones held here, which a system
administrator changes (ADR-013).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.auth.claims import Identity
from firebid.db.models.core import AppUser, BidMember, Organisation, UserRole
from firebid.domain.actors import Actor
from firebid.domain.state_machines import Role


class SwitchedOff(PermissionError):
    """The person is known and a system administrator has switched their access off."""


@dataclass(frozen=True)
class Principal:
    """The signed-in person, as the application sees them."""

    user_id: uuid.UUID
    organisation_id: uuid.UUID
    username: str
    display_name: str
    roles: frozenset[str]

    def actor(self) -> Actor:
        return Actor(label=self.display_name, roles=self.roles, id=self.user_id)


def default_organisation(session: Session, name: str) -> Organisation:
    """This is a single-organisation installation; the first sign-in creates it."""
    organisation = (
        session.execute(select(Organisation).order_by(Organisation.created_at)).scalars().first()
    )
    if organisation is None:
        organisation = Organisation(name=name)
        session.add(organisation)
        session.flush()
    return organisation


def provision(session: Session, identity: Identity, organisation: Organisation) -> Principal:
    user = session.execute(
        select(AppUser).where(AppUser.external_id == identity.external_id)
    ).scalar_one_or_none()

    if user is None:
        user = AppUser(
            organisation_id=organisation.id,
            external_id=identity.external_id,
            username=identity.username,
            display_name=identity.display_name,
        )
        session.add(user)
        session.flush()
        # The roles the identity provider gives someone are where they start.
        for role in identity.application_roles:
            session.add(UserRole(user_id=user.id, role=role))
        session.flush()
    elif (user.username, user.display_name) != (identity.username, identity.display_name):
        user.username = identity.username
        user.display_name = identity.display_name
    if not user.is_active:
        raise SwitchedOff(identity.username)

    roles = set(
        session.execute(select(UserRole.role).where(UserRole.user_id == user.id)).scalars().all()
    )
    admin = str(Role.SYSTEM_ADMIN)
    if (
        admin in identity.application_roles
        and admin not in roles
        and not _has_an_admin(session, organisation.id)
    ):
        # Nobody here can change anyone's roles: the identity provider's word is taken, so
        # an installation that has lost its administrators can be given one.
        session.add(UserRole(user_id=user.id, role=admin))
        roles.add(admin)
    session.flush()

    return Principal(
        user_id=user.id,
        organisation_id=user.organisation_id,
        username=user.username,
        display_name=user.display_name,
        roles=frozenset(roles),
    )


def _has_an_admin(session: Session, organisation_id: uuid.UUID) -> bool:
    return (
        session.execute(
            select(AppUser.id)
            .join(UserRole, UserRole.user_id == AppUser.id)
            .where(
                AppUser.organisation_id == organisation_id,
                AppUser.is_active.is_(True),
                UserRole.role == str(Role.SYSTEM_ADMIN),
            )
            .limit(1)
        ).first()
        is not None
    )


def is_member(session: Session, bid_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    return (
        session.execute(
            select(BidMember.bid_id).where(BidMember.bid_id == bid_id, BidMember.user_id == user_id)
        ).scalar_one_or_none()
        is not None
    )
