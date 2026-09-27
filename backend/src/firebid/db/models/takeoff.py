"""Detections, quantities, evidence and measurement rules.

`detected_object`, `qto_item` and `evidence` grow to millions of rows, so they are
hash-partitioned by `bid_id`. PostgreSQL requires the partition key inside the primary key,
which is why these tables have composite keys and composite foreign keys.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
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
from firebid.db.mixins import CreatedBy, Timestamped, personal
from firebid.db.types import LengthMmType
from firebid.domain.state_machines import QtoItemState
from firebid.domain.values import LengthMm

QTO_STATES = tuple(str(state) for state in QtoItemState)


DETECTION_KINDS = ("object", "riser", "drop")
# FR-VIS-07/09: located, or saying why the sheet cannot locate it.
LOCATED_OR_SAYS_WHY = (
    "(view_id IS NOT NULL OR gaps ? 'view') "
    "AND (grid_reference IS NOT NULL OR gaps ? 'grid_reference')"
)


class DetectedObject(Timestamped, Base):
    """Something found on a sheet, before it becomes a quantity. Always a proposal.

    FR-VIS-09: every one says how it was found (`extraction_method`), what from
    (`source_ref`), and how sure the platform is once calibrated (`confidence`); and, from
    FR-VIS-07, its view and grid reference, unless `gaps` records why the sheet cannot give
    one (a sheet with no grid, a symbol outside every view). The database enforces it.
    """

    __tablename__ = "detected_object"
    __table_args__ = (
        CheckConstraint(f"kind IN {DETECTION_KINDS}", name="kind_known"),
        CheckConstraint(LOCATED_OR_SAYS_WHY, name="located_or_says_why"),
        {"postgresql_partition_by": "HASH (bid_id)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    bid_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("bid.id", ondelete="CASCADE"), primary_key=True
    )
    sheet_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    sheet_revision_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    object_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    attributes: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    geometry_ref: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    source_ref: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    view_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # P1-05: what it is on the drawing, where, and how its confidence was reached.
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="object")
    grid_reference: Mapped[str | None] = mapped_column(String(40))
    level: Mapped[str | None] = mapped_column(String(40))
    orientation: Mapped[float | None] = mapped_column(Float)
    raw_confidence: Mapped[float | None] = mapped_column(Float)
    features: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    calibration_version: Mapped[str | None] = mapped_column(String(40))
    detector_version: Mapped[str | None] = mapped_column(String(16))
    gaps: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")


class QtoItem(Timestamped, CreatedBy, Base):
    """A counted or measured quantity. Verified and baselined rows are versioned, never edited."""

    __tablename__ = "qto_item"
    __table_args__ = (
        UniqueConstraint("bid_id", "human_id", name="uq_qto_item_human_id"),
        CheckConstraint(f"state IN {QTO_STATES}", name="state_known"),
        ForeignKeyConstraint(
            ["supersedes_id", "bid_id"],
            ["qto_item.id", "qto_item.bid_id"],
            name="fk_qto_item_supersedes",
            ondelete="SET NULL",
        ),
        {"postgresql_partition_by": "HASH (bid_id)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    bid_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("bid.id", ondelete="CASCADE"), primary_key=True
    )
    human_id: Mapped[str] = mapped_column(String(20), nullable=False)
    item_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    classification: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    attributes: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    net_quantity: Mapped[Decimal] = mapped_column(Numeric(16, 3), nullable=False)
    allowance_percent: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    length: Mapped[LengthMm | None] = mapped_column(LengthMmType)
    level: Mapped[str | None] = mapped_column(String(40), index=True)
    zone: Mapped[str | None] = mapped_column(String(40))
    grid_from: Mapped[str | None] = mapped_column(String(40))
    grid_to: Mapped[str | None] = mapped_column(String(40))
    calculation_method: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_key: Mapped[str | None] = mapped_column(String(80))
    rule_version: Mapped[int | None] = mapped_column(Integer)
    is_manual: Mapped[bool] = mapped_column(default=False, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=str(QtoItemState.DETECTED), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    duplicate_group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    verified_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="SET NULL")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason_code: Mapped[str | None] = mapped_column(String(40))


class Evidence(Timestamped, Base):
    """The Appendix B record behind a quantity. One per QTO item, stored as JSONB."""

    __tablename__ = "evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["qto_item_id", "bid_id"],
            ["qto_item.id", "qto_item.bid_id"],
            name="fk_evidence_qto_item",
            ondelete="CASCADE",
        ),
        {"postgresql_partition_by": "HASH (bid_id)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    bid_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("bid.id", ondelete="CASCADE"), primary_key=True
    )
    qto_item_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    record: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    missing_fields: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)


class MeasurementRule(Timestamped, CreatedBy, Base):
    """A versioned takeoff rule (drops, risers, fittings, allowances). FR-ADM-02."""

    __tablename__ = "measurement_rule"
    __table_args__ = (
        UniqueConstraint("organisation_id", "key", "version", name="uq_rule_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organisation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    definition: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    source_note: Mapped[str | None] = mapped_column(
        Text, info=personal("may name the estimator whose judgement set the rule")
    )
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PipeRun(Timestamped, Base):
    """One pipe run on a sheet: its class, size and length, as a proposal (FR-VIS-03, 06).

    `length_mm` is set only when the run's view is measurable (FR-VIS-05); otherwise the run
    keeps its paper length and `gaps` says why there is no length.
    """

    __tablename__ = "pipe_run"
    __table_args__ = (
        CheckConstraint("run_class IN ('main', 'branch')", name="class_known"),
        CheckConstraint(
            "size_status IN ('labelled', 'propagated', 'conflict', 'unknown')",
            name="size_status_known",
        ),
        CheckConstraint("size_status <> 'conflict' OR nominal_dn IS NULL", name="conflict_unsized"),
        CheckConstraint(LOCATED_OR_SAYS_WHY, name="located_or_says_why"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    bid_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("bid.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sheet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sheet.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sheet_revision_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    view_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    run_index: Mapped[int] = mapped_column(Integer, nullable=False)
    run_class: Mapped[str] = mapped_column(String(16), nullable=False)
    nominal_dn: Mapped[int | None] = mapped_column(Integer)
    size_status: Mapped[str] = mapped_column(String(16), nullable=False)
    size_reason: Mapped[str | None] = mapped_column(Text)
    labels: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list, nullable=False)
    paper_length_mm: Mapped[float] = mapped_column(Float, nullable=False)
    length_mm: Mapped[float | None] = mapped_column(Float)
    points: Mapped[list[list[float]]] = mapped_column(JSONB, nullable=False)
    geometry_rows: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    grid_reference: Mapped[str | None] = mapped_column(String(40))
    level: Mapped[str | None] = mapped_column(String(40))
    features: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    raw_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    calibration_version: Mapped[str] = mapped_column(String(40), nullable=False)
    detector_version: Mapped[str] = mapped_column(String(16), nullable=False)
    gaps: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")
