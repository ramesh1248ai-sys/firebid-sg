"""Symbols: the canonical object library, consultant mappings, legends and instances (P1-04).

Two organisation-level libraries, both versioned (FR-ADM-02): an edit writes a new row with
the next `version` and `supersedes_id`, and the old row is never changed.

* `object_type`: what can be taken off a drawing (a pendent sprinkler, a gate valve).
* `symbol_mapping`: how one consultant draws one of them. Versions of one mapping share a
  `lineage_id`. A mapping starts as a proposal (by rule, model or reuse) and only a person
  confirms it (guardrail 2).

Two bid-level tables, under row-level security like every tender record:

* `legend_entry`: a legend row on a sheet, its symbol's signature and description.
* `symbol_instance`: a symbol found on a drawing, and which legend row or mapping it matches.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from firebid.db.base import Base
from firebid.db.mixins import BidScoped, CreatedBy, Timestamped, UuidPk

MAPPING_STATES = ("proposed", "confirmed", "rejected")
MAPPING_SOURCES = ("rule", "model", "reuse", "person")


class ObjectType(UuidPk, Timestamped, CreatedBy, Base):
    """One version of one canonical object type."""

    __tablename__ = "object_type"
    __table_args__ = (UniqueConstraint("organisation_id", "key", "version"),)

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organisation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    # count | length | none: how the QTO engine takes it off.
    measure: Mapped[str] = mapped_column(String(16), nullable=False)
    attribute_schema: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    deprecated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("object_type.id", ondelete="SET NULL")
    )
    change_note: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[str | None] = mapped_column(String(200))


class SymbolMapping(UuidPk, Timestamped, CreatedBy, Base):
    """One version of how one consultant draws one object type."""

    __tablename__ = "symbol_mapping"
    __table_args__ = (
        UniqueConstraint("lineage_id", "version"),
        CheckConstraint(f"state IN {MAPPING_STATES}", name="state_known"),
        CheckConstraint(f"source IN {MAPPING_SOURCES}", name="source_known"),
        CheckConstraint(
            "state <> 'confirmed' OR confirmed_by IS NOT NULL", name="confirmation_named"
        ),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organisation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lineage_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("symbol_mapping.id", ondelete="SET NULL")
    )
    # Normalised, so "ALPHA CONSULTANTS PTE LTD" and "Alpha Consultants" are one consultant.
    consultant_key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    consultant: Mapped[str] = mapped_column(String(200), nullable=False)
    # A project override applies to that project only, and wins over the consultant's own.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), index=True
    )
    signature: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    description: Mapped[str | None] = mapped_column(String(300))
    object_type_key: Mapped[str | None] = mapped_column(String(80))
    attributes: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    # model, prompt version, rule version, confidence, agent run, input hash.
    provenance: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    confirmed_by: Mapped[str | None] = mapped_column(String(200))
    confirmed_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    change_note: Mapped[str | None] = mapped_column(Text)


class LegendEntry(UuidPk, BidScoped, Timestamped, Base):
    """A row of a legend: a symbol, what the consultant says it is, and what it maps to."""

    __tablename__ = "legend_entry"
    __table_args__ = (UniqueConstraint("sheet_id", "ordinal"),)

    sheet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sheet.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    # Sheet mm, y down.
    symbol_box: Mapped[list[float]] = mapped_column(JSONB, nullable=False)
    row_box: Mapped[list[float]] = mapped_column(JSONB, nullable=False)
    signature: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    crop_key: Mapped[str | None] = mapped_column(String(512))
    # The mapping lineage this row resolves to, confirmed or proposed.
    mapping_lineage_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    # reused | proposed | confirmed | rejected
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class SymbolInstance(BidScoped, Base):
    """A symbol drawn on a sheet. What it is comes from its mapping, looked up when counted."""

    __tablename__ = "symbol_instance"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    sheet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sheet.id", ondelete="CASCADE"), nullable=False, index=True
    )
    view_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # Symbols that are the same symbol share a key: `lineage:<id>` when matched to a mapping,
    # `legend:<id>` when matched to a legend row not yet mapped, else `block:` or `shape:`.
    symbol_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    legend_entry_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    mapping_lineage_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    block: Mapped[str | None] = mapped_column(String(200))
    cx: Mapped[float] = mapped_column(Float, nullable=False)
    cy: Mapped[float] = mapped_column(Float, nullable=False)
    bbox: Mapped[list[float]] = mapped_column(JSONB, nullable=False)
    rotation: Mapped[float | None] = mapped_column(Float)
    scale: Mapped[float | None] = mapped_column(Float)
    match_distance: Mapped[float | None] = mapped_column(Float)
    # Kept so an instance found before its legend was read can be matched once it is.
    signature: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    detector_version: Mapped[str] = mapped_column(String(16), nullable=False)
