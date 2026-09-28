"""The verification workbench's records (P1-08).

* `review_action`: one person's action on the takeoff (accept, edit, reject, reject
  detections, undo), with every item it touched and each item's state and values before and
  after. It is what undo reverses, and it is written beside the audit events, not instead.
* `correction_event`: a labelled correction for the evaluation harness (FR-REV-06): what the
  platform proposed, what a person made of it, why, and which detector and calibration
  produced the proposal. Derived labels only, never document text or images (requirements
  §11.4).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from firebid.db.base import Base
from firebid.db.mixins import BidScoped, Timestamped, UuidPk, personal

ACTION_KINDS = ("accept", "edit", "reject", "reject_detections", "undo")
CORRECTION_KINDS = ("edit", "reject", "false_detection")


class ReviewAction(UuidPk, BidScoped, Timestamped, Base):
    __tablename__ = "review_action"
    __table_args__ = (CheckConstraint(f"kind IN {ACTION_KINDS}", name="kind_known"),)

    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="SET NULL")
    )
    actor_label: Mapped[str] = mapped_column(
        String(200), nullable=False, info=personal("who took the action")
    )
    reason_code: Mapped[str | None] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(Text)
    # [{item_id | detection_id, before: {...}, after: {...}}]
    entries: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    undoes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("review_action.id", ondelete="SET NULL")
    )
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CorrectionEvent(UuidPk, BidScoped, Timestamped, Base):
    __tablename__ = "correction_event"
    __table_args__ = (CheckConstraint(f"kind IN {CORRECTION_KINDS}", name="kind_known"),)

    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    action_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("review_action.id", ondelete="SET NULL")
    )
    qto_item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    detection_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    object_type: Mapped[str] = mapped_column(String(80), nullable=False)
    before: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    after: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    reason_code: Mapped[str] = mapped_column(String(40), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    detector_method: Mapped[str | None] = mapped_column(String(40))
    detector_version: Mapped[str | None] = mapped_column(String(40))
    calibration_version: Mapped[str | None] = mapped_column(String(40))
    rule_version: Mapped[str | None] = mapped_column(String(200))
    confidence: Mapped[float | None] = mapped_column()
    consultant_key: Mapped[str | None] = mapped_column(String(120))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="SET NULL")
    )
    # Requirements §11.4: only company-owned derived data is learnt from across bids.
    data_policy: Mapped[str] = mapped_column(
        String(40), nullable=False, default="derived-labels-only"
    )
