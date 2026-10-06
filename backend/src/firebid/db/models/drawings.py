"""Drawing understanding: each sheet's geometry, its spatial index and its views (FR-VIS)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from firebid.db.base import Base
from firebid.db.geometry_types import Box
from firebid.db.mixins import BidScoped, UuidPk


class SheetGeometry(UuidPk, BidScoped, Base):
    """Where one sheet's primitives are stored, how they were obtained, and what they hold."""

    __tablename__ = "sheet_geometry"

    sheet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sheet.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(16), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    method: Mapped[str] = mapped_column(String(24), nullable=False)
    counts: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    page: Mapped[list[float]] = mapped_column(JSONB, nullable=False)
    # Views the source defines exactly: a DXF viewport with its scale, or modelspace placed
    # at its stated scale. Detected views (P1-03 D) are stored separately.
    views: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB)
    seconds: Mapped[float | None] = mapped_column(Float)
    from_cache: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    note: Mapped[str | None] = mapped_column(Text)
    # A digest of everything the sheet's detection was last made from. The same digest
    # again means the stored detections stand, and the sheet is not detected again.
    detection_fingerprint: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GeometryFeature(BidScoped, Base):
    """A primitive worth finding by place, with its box: text, insert, circle, dimension."""

    __tablename__ = "geometry_feature"
    __table_args__ = (Index("ix_geometry_feature_bbox", "bbox", postgresql_using="gist"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    sheet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sheet.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The primitive's row in the sheet's geometry table, to fetch the rest of it from there.
    row: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    layer: Mapped[str | None] = mapped_column(String(120))
    label: Mapped[str | None] = mapped_column(String(300))
    bbox: Mapped[tuple[float, float, float, float]] = mapped_column(Box(), nullable=False)


class SheetView(UuidPk, BidScoped, Base):
    """One view on a sheet: its kind, where it is, its scale and whether lengths are allowed.

    `scale_status` decides measurement (FR-VIS-05): only `verified` and `calibrated` views are
    measured. A calibration is a person's act, so who made it is kept with it, and it survives
    the views being detected again as long as the view is still there.
    """

    __tablename__ = "sheet_view"
    __table_args__ = (
        UniqueConstraint("sheet_id", "ordinal"),
        CheckConstraint(
            "scale_status IN ('verified', 'unverified', 'conflicting', 'nts', 'calibrated')",
            name="scale_status_known",
        ),
        CheckConstraint(
            "scale_status NOT IN ('verified', 'calibrated') OR denominator IS NOT NULL",
            name="measurable_has_scale",
        ),
        CheckConstraint(
            "scale_status <> 'calibrated' OR calibrated_by IS NOT NULL",
            name="calibration_named",
        ),
    )

    sheet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sheet.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str | None] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    # Sheet millimetres, y down: [x0, y0, x1, y1].
    extent: Mapped[list[float]] = mapped_column(JSONB, nullable=False)
    level: Mapped[str | None] = mapped_column(String(40))
    stated_scale: Mapped[str | None] = mapped_column(String(80))
    stated_denominator: Mapped[float | None] = mapped_column(Float)
    scale_status: Mapped[str] = mapped_column(String(16), nullable=False)
    # The scale lengths are converted with; null unless the view is measurable.
    denominator: Mapped[float | None] = mapped_column(Float)
    scale_evidence: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    grid: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    # The view's extent in grid units, which is comparable between sheets (FR-VIS-08).
    grid_box: Mapped[list[float] | None] = mapped_column(JSONB)
    # The gridlines the view's bubbles mark, a row of bubbles at a time:
    # {"across": [[[label, x], ...], ...], "up": [[[label, y], ...], ...]}.
    # How one sheet's scale is checked against another's (FR-VIS-05).
    grid_marks: Mapped[dict[str, list[list[list[object]]]] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    detector_version: Mapped[str] = mapped_column(String(16), nullable=False)
    calibration: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    calibrated_by: Mapped[str | None] = mapped_column(String(200))
    calibrated_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    calibrated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
