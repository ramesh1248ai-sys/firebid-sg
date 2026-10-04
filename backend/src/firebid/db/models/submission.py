"""The frozen submission, the tender's outcome, and proposed library changes (P2-08).

* `submission_snapshot`: what was submitted, frozen at G4: where its manifest is kept, the
  manifest's hash, and the files exported with it. Append-only: the database refuses any
  change to a row (FR-PKG-03).
* `bid_outcome`: awarded, lost or withdrawn, with the awarded price where known, the reasons
  and what was learned of competitors (FR-LRN-02).
* `library_proposal`: a change proposed to the rate or productivity library. It changes
  nothing until an estimator approves it, and then applies as a new version (FR-LRN-03).
  Organisation-level, like the libraries.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from firebid.db.base import Base
from firebid.db.mixins import BidScoped, Timestamped, UuidPk, personal
from firebid.db.types import MoneyType
from firebid.domain.values import Money

OUTCOMES = ("awarded", "lost", "withdrawn")
PROPOSAL_LIBRARIES = ("rate", "productivity")
PROPOSAL_SOURCES = ("quotation", "outcome", "estimator")
PROPOSAL_STATES = ("proposed", "approved", "rejected")


class SubmissionSnapshot(UuidPk, BidScoped, Timestamped, Base):
    __tablename__ = "submission_snapshot"
    __table_args__ = (UniqueConstraint("bid_id", name="uq_submission_snapshot_bid"),)

    manifest_key: Mapped[str] = mapped_column(String(512), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # Each exported file: name, object key, sha256, size and media type.
    files: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    # How many records of each kind the manifest lists.
    record_counts: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False, default=dict)
    frozen_by: Mapped[str] = mapped_column(
        String(200), nullable=False, info=personal("the G4 approver who froze the submission")
    )
    frozen_by_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)


class BidOutcome(UuidPk, BidScoped, Timestamped, Base):
    __tablename__ = "bid_outcome"
    __table_args__ = (
        UniqueConstraint("bid_id", name="uq_bid_outcome_bid"),
        CheckConstraint(f"outcome IN {OUTCOMES}", name="outcome_known"),
    )

    outcome: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    awarded_price: Mapped[Money | None] = mapped_column(MoneyType)
    reasons: Mapped[str] = mapped_column(Text, nullable=False, default="")
    competitor_feedback: Mapped[str] = mapped_column(Text, nullable=False, default="")
    recorded_by: Mapped[str] = mapped_column(
        String(200), nullable=False, info=personal("who recorded the outcome")
    )
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LibraryProposal(UuidPk, Timestamped, Base):
    __tablename__ = "library_proposal"
    __table_args__ = (
        CheckConstraint(f"library IN {PROPOSAL_LIBRARIES}", name="library_known"),
        CheckConstraint(f"source IN {PROPOSAL_SOURCES}", name="source_known"),
        CheckConstraint(f"state IN {PROPOSAL_STATES}", name="state_known"),
        CheckConstraint("state = 'proposed' OR decided_by IS NOT NULL", name="decision_named"),
        # FR-LRN-03: a library entry comes only from an approved proposal.
        CheckConstraint("applied_entry_id IS NULL OR state = 'approved'", name="applied_approved"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organisation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    library: Mapped[str] = mapped_column(String(16), nullable=False)
    # The entry as proposed: for a rate, its item key, unit, rate, source and dates; for a
    # productivity figure, its item, unit, man-hours, trade and source.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed", index=True)
    proposed_by: Mapped[str] = mapped_column(
        String(200), nullable=False, info=personal("who proposed the change")
    )
    proposed_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_by: Mapped[str | None] = mapped_column(
        String(200), info=personal("the estimator who approved or rejected the change")
    )
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    # The library entry made when it was approved.
    applied_entry_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
