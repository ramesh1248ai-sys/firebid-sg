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
from firebid.db.mixins import BidScoped, CreatedBy, Timestamped, UuidPk, personal

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


OBLIGATION_STATES = ("proposed", "verified", "rejected")
ISSUE_STATES = ("open", "dismissed", "resolved")
ISSUE_CATEGORIES = ("conflict", "missing", "ambiguous")
SCOPE_STATUSES = ("included", "excluded", "by_others", "unclear")


class SpecObligation(UuidPk, BidScoped, Timestamped, Base):
    """Something the specification obliges the contractor to do (FR-SPEC-02): its category,
    what it says, any quantity it states, and the clause it cites. A proposal until a person
    confirms it."""

    __tablename__ = "spec_obligation"
    __table_args__ = (
        UniqueConstraint("document_revision_id", "key", name="uq_spec_obligation_key"),
        CheckConstraint(f"state IN {OBLIGATION_STATES}", name="state_known"),
        CheckConstraint(f"method IN {ATTRIBUTE_METHODS}", name="method_known"),
        CheckConstraint("state = 'proposed' OR decided_by IS NOT NULL", name="decision_named"),
    )

    document_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document_revision.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    clause_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("spec_clause.id", ondelete="SET NULL")
    )
    clause_number: Mapped[str] = mapped_column(String(40), nullable=False)
    system: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    quantities: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    citation_ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    citation_reason: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")
    provenance: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    decided_by: Mapped[str | None] = mapped_column(
        String(200), info=personal("who confirmed or rejected the obligation")
    )
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)


class SpecIssue(UuidPk, BidScoped, Timestamped, Base):
    """A conflict, missing item or ambiguity between the specification and the drawings
    (FR-SPEC-03), citing both. Open issues are the clarification candidates (P2-06)."""

    __tablename__ = "spec_issue"
    __table_args__ = (
        UniqueConstraint("bid_id", "key", name="uq_spec_issue_key"),
        CheckConstraint(f"state IN {ISSUE_STATES}", name="state_known"),
        CheckConstraint(f"category IN {ISSUE_CATEGORIES}", name="category_known"),
        CheckConstraint("state <> 'dismissed' OR decided_by IS NOT NULL", name="dismissal_named"),
    )

    key: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    rule: Mapped[str] = mapped_column(String(60), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    system: Mapped[str | None] = mapped_column(String(24))
    # Both sides, as cited: document, revision, clause and words; sheet, revision and note.
    spec_ref: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    drawing_ref: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    detail: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    rules_version: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    decided_by: Mapped[str | None] = mapped_column(
        String(200), info=personal("who dismissed the issue")
    )
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    last_found_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScopeRow(UuidPk, BidScoped, Timestamped, Base):
    """One row of the scope and interface matrix (FR-SPEC-04): an obligation or interface
    for a system, whose it is, and the clause that says so. Proposed by rule; a person may
    change the status, and confirms the matrix."""

    __tablename__ = "scope_row"
    __table_args__ = (
        UniqueConstraint("bid_id", "system", "kind", "key", name="uq_scope_row"),
        CheckConstraint(f"status IN {SCOPE_STATUSES}", name="status_known"),
        CheckConstraint("kind IN ('obligation', 'interface')", name="kind_known"),
        CheckConstraint("source IN ('rule', 'person')", name="source_known"),
    )

    system: Mapped[str] = mapped_column(String(24), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    proposed_status: Mapped[str] = mapped_column(String(16), nullable=False)
    document_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("document_revision.id", ondelete="SET NULL")
    )
    clause_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("spec_clause.id", ondelete="SET NULL")
    )
    clause_number: Mapped[str | None] = mapped_column(String(40))
    quote: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="rule")
    note: Mapped[str | None] = mapped_column(Text)
    edited_by: Mapped[str | None] = mapped_column(
        String(200), info=personal("who changed the row's status")
    )
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[str | None] = mapped_column(
        String(200), info=personal("who confirmed the matrix")
    )
    confirmed_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
