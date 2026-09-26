"""Documents, sheets, drawing revisions and addenda."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from firebid.db.base import Base
from firebid.db.mixins import BidScoped, CreatedBy, Timestamped, UuidPk, personal
from firebid.domain.state_machines import SheetRevisionState

REVISION_STATES = tuple(str(state) for state in SheetRevisionState)
DOCUMENT_STATES = ("received", "awaiting_scan", "quarantined", "processing", "done", "rejected")


class Document(UuidPk, BidScoped, Timestamped, CreatedBy, Base):
    """An uploaded file, stored once and never overwritten."""

    __tablename__ = "document"
    __table_args__ = (
        UniqueConstraint("bid_id", "sha256", name="uq_document_bid_sha256"),
        CheckConstraint(f"state IN {DOCUMENT_STATES}", name="state_known"),
    )

    tender_package_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tender_package.id", ondelete="SET NULL"), index=True
    )
    filename: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        info=personal("a consultant's file name can contain a person's name"),
    )
    media_type: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str | None] = mapped_column(String(40), index=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="received", index=True)
    rejected_reason: Mapped[str | None] = mapped_column(Text)
    # Evidence that the scan happened, kept separately from `state` so "cleared at 10:04 by
    # signature set X" survives a later state change (NFR-06).
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scan_signature: Mapped[str | None] = mapped_column(String(200))
    derived_from_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("document.id", ondelete="SET NULL")
    )


class Sheet(UuidPk, BidScoped, Timestamped, Base):
    """One page of a PDF or one layout of a CAD file."""

    __tablename__ = "sheet"
    __table_args__ = (UniqueConstraint("document_id", "index_in_document", name="uq_sheet_index"),)

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document.id", ondelete="CASCADE"), nullable=False, index=True
    )
    index_in_document: Mapped[int] = mapped_column(Integer, nullable=False)
    layout_name: Mapped[str | None] = mapped_column(String(120))
    width_mm: Mapped[float | None] = mapped_column(Float)
    height_mm: Mapped[float | None] = mapped_column(Float)
    content_class: Mapped[str | None] = mapped_column(String(16))  # vector | raster | mixed
    quality_band: Mapped[str | None] = mapped_column(String(16))  # high | medium | low
    manual_takeoff_recommended: Mapped[bool] = mapped_column(default=False, nullable=False)
    quality_detail: Mapped[dict[str, object] | None] = mapped_column(JSONB)

    # Where this sheet came from (FR-DOC-07). Every quantity measured on it inherits this.
    source_ref: Mapped[dict[str, object] | None] = mapped_column(JSONB)

    # The tile pyramid. `content_hash` keys the tiles in object storage and is derived from
    # the document's digest and this page, so identical sheets share one set of tiles across
    # bids; the API checks membership before serving any of them.
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    base_width_px: Mapped[int | None] = mapped_column(Integer)
    base_height_px: Mapped[int | None] = mapped_column(Integer)
    max_level: Mapped[int | None] = mapped_column(Integer)
    renderer_version: Mapped[str | None] = mapped_column(String(16))
    thumbnail_key: Mapped[str | None] = mapped_column(String(512))


class SheetRevision(UuidPk, BidScoped, Timestamped, CreatedBy, Base):
    """A drawing number at one revision, pointing at the sheet that carries it.

    Only rows in state `current` feed takeoff (guardrail 6).
    """

    __tablename__ = "sheet_revision"
    __table_args__ = (
        UniqueConstraint("bid_id", "sheet_number", "revision_label", name="uq_sheet_revision"),
        CheckConstraint(f"state IN {REVISION_STATES}", name="state_known"),
    )

    sheet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sheet.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sheet_number: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(300))
    revision_label: Mapped[str] = mapped_column(String(40), nullable=False)
    revision_date: Mapped[date | None] = mapped_column()
    discipline: Mapped[str | None] = mapped_column(String(40))
    level: Mapped[str | None] = mapped_column(String(40))
    zone: Mapped[str | None] = mapped_column(String(40))
    scale_text: Mapped[str | None] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=str(SheetRevisionState.RECEIVED), index=True
    )
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("sheet_revision.id", ondelete="SET NULL")
    )
    addendum_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("addendum.id", ondelete="SET NULL"), index=True
    )
    source_confidence: Mapped[float | None] = mapped_column(Float)
    extraction_method: Mapped[str | None] = mapped_column(String(24))


class Addendum(UuidPk, BidScoped, Timestamped, CreatedBy, Base):
    """An addendum or clarification response issued during the tender (FR-DOC-05)."""

    __tablename__ = "addendum"
    __table_args__ = (UniqueConstraint("bid_id", "number", name="uq_addendum_number"),)

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    issued_on: Mapped[date | None] = mapped_column()
    tender_package_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tender_package.id", ondelete="SET NULL")
    )
    summary: Mapped[str | None] = mapped_column(Text)
