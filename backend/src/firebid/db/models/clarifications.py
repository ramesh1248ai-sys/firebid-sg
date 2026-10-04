"""Tender clarifications and the qualifications they become (P2-06).

* `clarification`: a question for the client, drafted from flagged issues, with the
  evidence it rests on. Its lifecycle is the clarification model of requirements §7. The
  `kind` column reserves `construction_rfi` for after award, which is out of scope: nothing
  creates one (FR-RFI-01).
* `clarification_source`: the flagged issues a clarification was drafted from. An issue is
  in one clarification at most.
* `qualification`: a qualification or assumption proposed for the tender, from a
  clarification still unresolved when the submission is prepared (FR-RFI-05).

All bid-scoped, under row-level security.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
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

CLARIFICATION_KINDS = ("tender_clarification", "construction_rfi")
CLARIFICATION_STATES = (
    "draft",
    "internal_review",
    "approved_to_issue",
    "issued",
    "responded",
    "closed_incorporated",
    "closed_no_change",
    "converted_to_qualification",
)
SOURCE_KINDS = ("spec_issue", "boq_variance", "scope_row", "missing_information")
IMPACT_OUTCOMES = ("incorporated", "no_change")
QUALIFICATION_KINDS = ("qualification", "assumption")
QUALIFICATION_STATES = ("proposed", "accepted", "rejected")


class Clarification(UuidPk, BidScoped, Timestamped, CreatedBy, Base):
    __tablename__ = "clarification"
    __table_args__ = (
        UniqueConstraint("bid_id", "number", name="uq_clarification_number"),
        CheckConstraint(f"kind IN {CLARIFICATION_KINDS}", name="kind_known"),
        CheckConstraint(f"state IN {CLARIFICATION_STATES}", name="state_known"),
        # Guardrail 5: a clarification rests on at least one evidence reference.
        CheckConstraint("jsonb_array_length(evidence) >= 1", name="has_evidence"),
        CheckConstraint(
            "required_reviewer IN ('bid_manager', 'design_manager')", name="reviewer_known"
        ),
        CheckConstraint(
            f"impact_outcome IS NULL OR impact_outcome IN {IMPACT_OUTCOMES}", name="outcome_known"
        ),
    )

    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="tender_clarification")
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    project: Mapped[str] = mapped_column(String(300), nullable=False)
    level_grid: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    sheets: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    # Each option is a recommendation for the client to decide on (FR-RFI-06).
    options: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    cost_impact: Mapped[str] = mapped_column(Text, nullable=False, default="")
    programme_impact: Mapped[str] = mapped_column(Text, nullable=False, default="")
    required_reviewer: Mapped[str] = mapped_column(String(24), nullable=False)
    engineering_content: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    engineering_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    qp_input_needed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # How it was drafted: by the rules, or with the model (provider, model, prompt, run).
    drafting: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    design_approved_by: Mapped[str | None] = mapped_column(
        String(200), info=personal("the Design Manager who approved the engineering content")
    )
    design_approved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    design_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    design_note: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(
        String(200), info=personal("the Bid Manager who approved it for issue")
    )
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_by: Mapped[str | None] = mapped_column(
        String(200), info=personal("who recorded that it was sent to the client")
    )
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_summary: Mapped[str | None] = mapped_column(Text)
    response_document_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("document.id", ondelete="SET NULL")
    )
    impact_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("human_task.id", ondelete="SET NULL")
    )
    impact_outcome: Mapped[str | None] = mapped_column(String(16))
    impact_note: Mapped[str | None] = mapped_column(Text)
    impact_detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    impact_by: Mapped[str | None] = mapped_column(
        String(200), info=personal("who assessed the response's impact")
    )
    impact_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClarificationSource(UuidPk, BidScoped, Timestamped, Base):
    __tablename__ = "clarification_source"
    __table_args__ = (
        UniqueConstraint("bid_id", "kind", "ref", name="uq_clarification_source"),
        CheckConstraint(f"kind IN {SOURCE_KINDS}", name="kind_known"),
    )

    clarification_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("clarification.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    ref: Mapped[str] = mapped_column(String(200), nullable=False)
    # The issue as it stood when the clarification was drafted.
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class Qualification(UuidPk, BidScoped, Timestamped, Base):
    __tablename__ = "qualification"
    __table_args__ = (
        UniqueConstraint("clarification_id", name="uq_qualification_clarification"),
        CheckConstraint(f"kind IN {QUALIFICATION_KINDS}", name="kind_known"),
        CheckConstraint(f"state IN {QUALIFICATION_STATES}", name="state_known"),
        CheckConstraint("state = 'proposed' OR decided_by IS NOT NULL", name="decision_named"),
    )

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # The unresolved clarification it was proposed from (FR-RFI-05).
    clarification_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("clarification.id", ondelete="SET NULL"), index=True
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")
    decided_by: Mapped[str | None] = mapped_column(
        String(200), info=personal("who accepted or rejected the qualification")
    )
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
