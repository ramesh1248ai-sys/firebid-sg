"""Documents, sheets, drawing revisions and addenda."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from firebid.db.base import Base
from firebid.db.mixins import BidScoped, CreatedBy, Timestamped, UuidPk, personal
from firebid.domain.state_machines import SheetRevisionState
from firebid.ingest.classification import DocType

REVISION_STATES = tuple(str(state) for state in SheetRevisionState)
DOC_TYPES = tuple(str(doc_type) for doc_type in DocType)
DOCUMENT_STATES = ("received", "awaiting_scan", "quarantined", "processing", "done", "rejected")
ORIGINS = ("tender", "working", "reference")


class Document(UuidPk, BidScoped, Timestamped, CreatedBy, Base):
    """An uploaded file, stored once and never overwritten."""

    __tablename__ = "document"
    __table_args__ = (
        UniqueConstraint("bid_id", "sha256", name="uq_document_bid_sha256"),
        CheckConstraint(f"state IN {DOCUMENT_STATES}", name="state_known"),
        CheckConstraint(f"doc_type IS NULL OR doc_type IN {DOC_TYPES}", name="doc_type_known"),
        CheckConstraint(f"origin IN {ORIGINS}", name="origin_known"),
        CheckConstraint("origin_status IN ('proposed', 'confirmed')", name="origin_status_known"),
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
    # What kind of tender document it is (FR-DOC-02): a proposal until a person confirms it.
    doc_type: Mapped[str | None] = mapped_column(String(32), index=True)
    doc_type_confidence: Mapped[float | None] = mapped_column(Float)
    # Who or what proposed it, why, and the digest it was decided from.
    classification: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    # Folder intake (FR-DOC-09, 10): where the file sat in the folder it came from, and
    # whose document it is. Only a tender document whose origin is confirmed is read; a
    # `proposed` origin waits for a person.
    source_path: Mapped[str | None] = mapped_column(
        String(1024), info=personal("a folder or file name can contain a person's name")
    )
    origin: Mapped[str] = mapped_column(
        String(16), nullable=False, default="tender", server_default="tender", index=True
    )
    origin_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="confirmed", server_default="confirmed"
    )
    origin_reason: Mapped[str | None] = mapped_column(Text)
    origin_by: Mapped[str | None] = mapped_column(
        String(200), info=personal("the name of the person who confirmed the origin")
    )
    origin_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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

    # Its own parse job's outcome (ADR-010): when it finished, and why it failed if it did.
    # A failed sheet is still finished, so one bad sheet never holds up its document.
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parse_error: Mapped[str | None] = mapped_column(Text)
    # When the sheet's own detection job finished (after its document's sheets were all
    # read), and why it failed if it did. A failed sheet is finished too, so its document
    # can finish.
    detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detection_error: Mapped[str | None] = mapped_column(Text)


class SheetRevision(UuidPk, BidScoped, Timestamped, CreatedBy, Base):
    """A drawing number at one revision, pointing at the sheet that carries it.

    Only rows in state `current` feed takeoff (guardrail 6).
    """

    __tablename__ = "sheet_revision"
    __table_args__ = (
        UniqueConstraint("bid_id", "sheet_number", "revision_label", name="uq_sheet_revision"),
        CheckConstraint(f"state IN {REVISION_STATES}", name="state_known"),
        # Absent beats invented: an unread number or revision stays null while the revision
        # waits for a person, or once a person has withdrawn it (a cover sheet, say). Nothing
        # unidentified is ever registered.
        CheckConstraint(
            "(sheet_number IS NOT NULL AND revision_label IS NOT NULL) "
            "OR state IN ('received', 'withdrawn')",
            name="identified_unless_received",
        ),
        # Guardrail 6 in the database too: two workers cannot both make a revision Current.
        Index(
            "uq_sheet_revision_one_current",
            "bid_id",
            "sheet_number",
            unique=True,
            postgresql_where=text("state = 'current'"),
        ),
    )

    sheet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sheet.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sheet_number: Mapped[str | None] = mapped_column(String(120), index=True)
    title: Mapped[str | None] = mapped_column(String(300))
    revision_label: Mapped[str | None] = mapped_column(String(40))
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
    # How each field was read: value, confidence, method and position (a proposal's provenance).
    reading: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    # What each source said the revision was: title block, filename, transmittal.
    sources: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    conflict_reason: Mapped[str | None] = mapped_column(Text)
    # The same drawing supplied again in another format: one revision, several renditions.
    alternate_sheet_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )


class TitleBlockLayout(UuidPk, Timestamped, CreatedBy, Base):
    """Where one consultant puts each title block field, learnt from a confirmed reading.

    Organisation-level: it holds positions and a consultant's name, not tender content.
    """

    __tablename__ = "title_block_layout"

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organisation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    consultant: Mapped[str] = mapped_column(String(200), nullable=False)
    fingerprint: Mapped[list[list[object]]] = mapped_column(JSONB, nullable=False)
    layout: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    times_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


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


class TransmittalEntry(UuidPk, BidScoped, Base):
    """One line of a drawing list or transmittal: what it says was issued (FR-DOC-04)."""

    __tablename__ = "transmittal_entry"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "sheet_number", "revision_label", name="uq_transmittal_entry_document_id"
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document.id", ondelete="CASCADE"), nullable=False
    )
    tender_package_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tender_package.id", ondelete="SET NULL")
    )
    sheet_number: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    revision_label: Mapped[str] = mapped_column(String(40), nullable=False)
    issued_on: Mapped[date | None] = mapped_column()
    title: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DocumentRevision(UuidPk, BidScoped, Timestamped, CreatedBy, Base):
    """A non-drawing document at one revision: the specification register's row (FR-DOC-03).

    Follows the same state machine as a sheet revision. `doc_key` is the document's identity,
    its document number or, failing that, its title, so revisions of one document compete for
    Current and different documents do not.
    """

    __tablename__ = "document_revision"
    __table_args__ = (
        CheckConstraint(f"state IN {REVISION_STATES}", name="state_known"),
        CheckConstraint(
            "doc_key IS NOT NULL OR state IN ('received', 'withdrawn')",
            name="identified_unless_received",
        ),
        Index(
            "uq_document_revision_one_current",
            "bid_id",
            "doc_key",
            unique=True,
            postgresql_where=text("state = 'current'"),
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    doc_type: Mapped[str | None] = mapped_column(String(32))
    doc_key: Mapped[str | None] = mapped_column(String(200), index=True)
    title: Mapped[str | None] = mapped_column(String(300))
    revision_label: Mapped[str | None] = mapped_column(String(40))
    revision_date: Mapped[date | None] = mapped_column()
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=str(SheetRevisionState.RECEIVED), index=True
    )
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("document_revision.id", ondelete="SET NULL")
    )
    addendum_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("addendum.id", ondelete="SET NULL")
    )
    sources: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    conflict_reason: Mapped[str | None] = mapped_column(Text)


class RegisterConfirmation(UuidPk, BidScoped, Timestamped, Base):
    """Stage S1's output: an Estimator confirmed the registers, and what they held then."""

    __tablename__ = "register_confirmation"

    confirmed_by_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    confirmed_role: Mapped[str] = mapped_column(String(40), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    drawings: Mapped[int] = mapped_column(Integer, nullable=False)
    documents: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
