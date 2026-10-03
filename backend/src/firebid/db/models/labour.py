"""The productivity library and a bid's site conditions (P2-05).

* `labour_productivity`: man-hours per unit of an item, with its trade and its source. The
  organisation's, and versioned like the rate library: an entry is never changed; a new
  figure for the same item is a new version, and the old one is kept.
* `labour_condition`: a multiplier of the catalogue (`config/labour.yaml`) proposed or
  confirmed for a bid or one of its levels. Only a confirmed one is applied, and it says who
  confirmed it. Bid-scoped, under row-level security.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from firebid.db.base import Base
from firebid.db.mixins import BidScoped, CreatedBy, Timestamped, UuidPk, personal

PRODUCTIVITY_SOURCES = ("company_standard", "historical_project", "estimator_judgement")
CONDITION_STATES = ("proposed", "confirmed", "rejected")


class LabourProductivity(UuidPk, Timestamped, CreatedBy, Base):
    __tablename__ = "labour_productivity"
    __table_args__ = (
        CheckConstraint(f"source_type IN {PRODUCTIVITY_SOURCES}", name="source_known"),
        # FR-LAB-01: every entry says which standard, which project or whose judgement.
        CheckConstraint("btrim(source_reference) <> ''", name="source_named"),
        CheckConstraint("hours_per_unit > 0", name="hours_positive"),
        Index(
            "uq_labour_productivity_current",
            "organisation_id",
            "item_type",
            "dn",
            "joining",
            "unit",
            unique=True,
            postgresql_where=text("retired_at IS NULL"),
        ),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organisation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_type: Mapped[str] = mapped_column(String(80), nullable=False)
    dn: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    joining: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    hours_per_unit: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    trade: Mapped[str] = mapped_column(String(40), nullable=False)
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_reference: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        info=personal("names the estimator when the source is an estimator's judgement"),
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("labour_productivity.id", ondelete="SET NULL")
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LabourCondition(UuidPk, BidScoped, Timestamped, Base):
    __tablename__ = "labour_condition"
    __table_args__ = (
        CheckConstraint(f"state IN {CONDITION_STATES}", name="state_known"),
        CheckConstraint("state = 'proposed' OR decided_by IS NOT NULL", name="decision_named"),
        Index(
            "uq_labour_condition",
            "bid_id",
            text("coalesce(level, '')"),
            "multiplier_key",
            unique=True,
        ),
    )

    # None: the whole bid.
    level: Mapped[str | None] = mapped_column(String(40))
    multiplier_key: Mapped[str] = mapped_column(String(60), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")
    # Why it was proposed or set: the parameter it follows from, or the estimator's note.
    basis: Mapped[str] = mapped_column(Text, nullable=False, default="")
    proposed_by: Mapped[str] = mapped_column(
        String(200), nullable=False, info=personal("who proposed it: the platform or a person")
    )
    decided_by: Mapped[str | None] = mapped_column(
        String(200), info=personal("who confirmed or rejected the multiplier")
    )
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
