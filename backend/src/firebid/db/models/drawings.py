"""Drawing understanding: each sheet's extracted geometry and its spatial index (FR-VIS-01)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
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
