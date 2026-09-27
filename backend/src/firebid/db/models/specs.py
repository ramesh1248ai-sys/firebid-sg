"""Specifications as clauses, and the attributes takeoff needs from them (P1-06).

* `spec_clause`: a specification revision's clause tree, each clause with its number,
  heading, text, anchor (page and line, or paragraph) and the system its section is about.
* `spec_attribute`: one attribute of one system over a DN range, with the clause it cites and
  the result of checking that citation. Versioned: an edit or confirmation is a new row with
  the next `version` of its `lineage_id`. Only a person makes one `verified` (guardrail 2),
  and the QTO engine reads only those.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
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
from firebid.db.mixins import BidScoped, CreatedBy, Timestamped, UuidPk

ATTRIBUTE_STATES = ("proposed", "verified", "rejected")
ATTRIBUTE_METHODS = ("rule", "model", "person")


class SpecClause(UuidPk, BidScoped, Timestamped, Base):
    __tablename__ = "spec_clause"
    __table_args__ = (UniqueConstraint("document_revision_id", "ordinal"),)

    document_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document_revision.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    heading: Mapped[str] = mapped_column(Text, nullable=False, default="")
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    parent: Mapped[str | None] = mapped_column(String(40))
    anchor: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    # sprinkler | hose_reel | hydrant | ... | general | other | unknown
    system: Mapped[str] = mapped_column(String(24), nullable=False)
    system_source: Mapped[str] = mapped_column(String(16), nullable=False, default="rule")


class SpecAttribute(UuidPk, BidScoped, Timestamped, CreatedBy, Base):
    __tablename__ = "spec_attribute"
    __table_args__ = (
        UniqueConstraint("lineage_id", "version"),
        CheckConstraint(f"state IN {ATTRIBUTE_STATES}", name="state_known"),
        CheckConstraint(f"method IN {ATTRIBUTE_METHODS}", name="method_known"),
        CheckConstraint("state <> 'verified' OR verified_by IS NOT NULL", name="verifier_named"),
    )

    lineage_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("spec_attribute.id", ondelete="SET NULL")
    )
    document_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document_revision.id", ondelete="CASCADE"), nullable=False, index=True
    )
    clause_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("spec_clause.id", ondelete="SET NULL")
    )
    clause_number: Mapped[str] = mapped_column(String(40), nullable=False)
    system: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    attribute: Mapped[str] = mapped_column(String(40), nullable=False)
    value: Mapped[str] = mapped_column(String(200), nullable=False)
    dn_min: Mapped[int | None] = mapped_column(Integer)
    dn_max: Mapped[int | None] = mapped_column(Integer)
    condition: Mapped[str | None] = mapped_column(String(200))
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    citation_ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    citation_reason: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    # rule version, or model, prompt version, agent run and confidence
    provenance: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    verified_by: Mapped[str | None] = mapped_column(String(200))
    verified_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    change_note: Mapped[str | None] = mapped_column(Text)
