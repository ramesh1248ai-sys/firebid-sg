"""Turning a verified identity into a user of this installation.

The identity provider owns identity; this application owns roles and bid membership. A person
is created on first sign-in, and their name and application roles are refreshed on each one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from firebid.auth.claims import Identity
from firebid.db.models.core import AppUser, BidMember, Organisation, UserRole
from firebid.domain.actors import Actor


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
    elif (user.username, user.display_name) != (identity.username, identity.display_name):
        user.username = identity.username
        user.display_name = identity.display_name

    roles = identity.application_roles
    existing = set(
        session.execute(select(UserRole.role).where(UserRole.user_id == user.id)).scalars().all()
    )
    for role in roles - existing:
        session.add(UserRole(user_id=user.id, role=role))
    if removed := existing - roles:
        session.execute(
            delete(UserRole).where(UserRole.user_id == user.id, UserRole.role.in_(removed))
        )
    session.flush()

    return Principal(
        user_id=user.id,
        organisation_id=user.organisation_id,
        username=user.username,
        display_name=user.display_name,
        roles=roles,
    )


def is_member(session: Session, bid_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    return (
        session.execute(
            select(BidMember.bid_id).where(BidMember.bid_id == bid_id, BidMember.user_id == user_id)
        ).scalar_one_or_none()
        is not None
    )
