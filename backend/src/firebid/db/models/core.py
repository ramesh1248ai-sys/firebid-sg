"""Organisation, people, projects and bids."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from firebid.db.base import Base
from firebid.db.mixins import BidScoped, CreatedBy, Timestamped, UuidPk, personal
from firebid.domain.state_machines import BidState, Role

BID_STATES = tuple(str(state) for state in BidState)
ROLES = tuple(str(role) for role in Role)
STAGES = tuple(f"S{index}" for index in range(10))  # requirements §5


class Organisation(UuidPk, Timestamped, Base):
    __tablename__ = "organisation"

    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    # How many days before a deadline people are warned (FR-BID-03).
    deadline_alert_days: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, default=lambda: [7, 3, 1]
    )


class AppUser(UuidPk, Timestamped, Base):
    """A person, identified by the IdP. Roles and bid membership live here, not in the IdP."""

    __tablename__ = "app_user"

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organisation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        info=personal("identifies the person at the identity provider (Entra ID 'oid')"),
    )
    username: Mapped[str] = mapped_column(
        String(256), nullable=False, info=personal("sign-in name, usually a work email address")
    )
    display_name: Mapped[str] = mapped_column(
        String(200), nullable=False, info=personal("shown on approvals and the audit trail")
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)


class UserRole(Base):
    """Organisation-level roles (requirements §3). Per-bid membership is in `bid_member`."""

    __tablename__ = "user_role"
    __table_args__ = (CheckConstraint(f"role IN {ROLES}", name="role_known"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(40), primary_key=True)


class Project(UuidPk, Timestamped, CreatedBy, Base):
    __tablename__ = "project"

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organisation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    developer: Mapped[str | None] = mapped_column(String(200))
    consultant: Mapped[str | None] = mapped_column(String(200))
    # The scheme in config/revisions.yaml that orders this project's revisions; None: default.
    revision_scheme: Mapped[str | None] = mapped_column(String(64))


class Bid(UuidPk, Timestamped, CreatedBy, Base):
    """One tender to one client. A project may hold several bids (FR-BID-04)."""

    __tablename__ = "bid"
    __table_args__ = (
        UniqueConstraint("organisation_id", "human_id", name="uq_bid_human_id"),
        CheckConstraint(f"state IN {BID_STATES}", name="state_known"),
        CheckConstraint(f"stage IN {STAGES}", name="stage_known"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organisation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    human_id: Mapped[str] = mapped_column(String(20), nullable=False)
    client_name: Mapped[str] = mapped_column(String(200), nullable=False)
    tender_reference: Mapped[str] = mapped_column(String(120), nullable=False)
    submission_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    clarification_cutoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tender_validity_days: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(
        String(40), nullable=False, default=str(BidState.REGISTERED), index=True
    )
    # Where the work has reached in the process (requirements §5): S0 intake to S9 post-tender.
    stage: Mapped[str] = mapped_column(String(4), nullable=False, default="S0", index=True)


class BidMember(Timestamped, Base):
    """Who may see and work on a bid (NFR-08; enforced again by row-level security in P0-03)."""

    __tablename__ = "bid_member"
    __table_args__ = (CheckConstraint(f"role IN {ROLES}", name="role_known"),)

    bid_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("bid.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(String(40), nullable=False)


class TenderPackage(UuidPk, BidScoped, Timestamped, CreatedBy, Base):
    """A set of documents received together: the original issue, or an addendum's files."""

    __tablename__ = "tender_package"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    received_on: Mapped[date | None] = mapped_column()
