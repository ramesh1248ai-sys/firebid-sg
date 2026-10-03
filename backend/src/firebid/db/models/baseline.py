"""Baselines, sampling and shared takeoffs (P2-02).

* `takeoff_snapshot`: a bid's takeoff as it stood at a moment (G1 approved, an addendum
  registered): each live item with its quantity, state and BOQ line. What a delta report
  compares the takeoff with (FR-QTO-12). Bid-scoped, under row-level security.
* `coverage_policy`: how one item category is covered at G1, full review or a sample
  (FR-REV-05). Organisation-level and versioned: a Senior Estimator's edit retires the
  version in force and adds the next.
* `review_sample`: one draw of a sample for a category of a bid: the lot, the sample, the
  seed it was drawn with, and what became of it. Bid-scoped.
* `shared_takeoff`: a bid's verified takeoff published to its project, for the project's
  other bids to adopt (FR-BID-04). Project-level: any member of any bid of the project may
  read it. It holds quantities and drawing references, never a client's documents or prices.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from firebid.db.base import Base
from firebid.db.mixins import BidScoped, CreatedBy, Timestamped, UuidPk, personal

POLICY_MODES = ("full", "sampling")
SAMPLE_STATUSES = ("drawn", "passed", "accepted", "escalated")


class TakeoffSnapshot(UuidPk, BidScoped, Timestamped, Base):
    __tablename__ = "takeoff_snapshot"

    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    addendum_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("addendum.id", ondelete="SET NULL"), index=True
    )
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("approval.id", ondelete="SET NULL")
    )
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # [{human_id, version, description, unit, quantity, state, inputs_hash, line, ...}]
    items: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)


class CoveragePolicy(UuidPk, Timestamped, CreatedBy, Base):
    __tablename__ = "coverage_policy"
    __table_args__ = (
        UniqueConstraint("organisation_id", "category", "version", name="uq_coverage_policy"),
        CheckConstraint(f"mode IN {POLICY_MODES}", name="mode_known"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organisation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    tolerable_error_percent: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    confidence_percent: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    accept_errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The accuracy evidence the category's move to sampling rests on.
    note: Mapped[str | None] = mapped_column(
        Text, info=personal("may name the estimator whose judgement set the policy")
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewSample(UuidPk, BidScoped, Timestamped, Base):
    __tablename__ = "review_sample"
    __table_args__ = (CheckConstraint(f"status IN {SAMPLE_STATUSES}", name="status_known"),)

    category: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    plan: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    # Human IDs: an item keeps its ID across versions, so the lot survives a recompute.
    lot: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    sample: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    seed: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="drawn")
    errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outcomes: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    drawn_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="SET NULL")
    )
    drawn_by: Mapped[str] = mapped_column(
        String(200), nullable=False, info=personal("who drew the sample")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(
        String(200), info=personal("who accepted the category on the sample")
    )


class SharedTakeoff(UuidPk, Timestamped, Base):
    __tablename__ = "shared_takeoff"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_shared_takeoff_version"),)

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organisation.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Named `source_bid_id`, not `bid_id`: the row is the project's, not one bid's.
    source_bid_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("bid.id", ondelete="CASCADE"), nullable=False
    )
    source_bid_human_id: Mapped[str] = mapped_column(String(20), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Each verified item in full, as a bid that adopts it will hold it.
    items: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    published_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="SET NULL")
    )
    published_by: Mapped[str] = mapped_column(
        String(200), nullable=False, info=personal("who published the takeoff to the project")
    )
    note: Mapped[str | None] = mapped_column(Text)
