"""The scope-gap checklist and the bid risk register (P2-07).

* `scope_check`: one item of the scope-gap checklist for one system of a bid, with the
  status the rules proposed and the status a person resolved it to (FR-RSK-01).
* `risk`: a design-responsibility or execution risk the rules found, with its evidence,
  its treatment, owner and status, and what it could cost: computed, then accepted or
  adjusted by an estimator with a reason (FR-RSK-02, 03, 04, 06).

Both bid-scoped, under row-level security. Qualifications are in `clarifications.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from firebid.db.base import Base
from firebid.db.mixins import BidScoped, Timestamped, UuidPk, personal
from firebid.db.types import MoneyType
from firebid.domain.values import Money

CHECK_STATUSES = ("open", "included", "excluded", "by_others", "clarified")
RISK_CATEGORIES = ("design_responsibility", "execution")
RISK_TREATMENTS = ("price", "qualify", "clarify", "accept")
RISK_STATUSES = ("open", "treated", "closed", "no_longer_found")
IMPACT_STATES = ("computed", "accepted", "adjusted")


class ScopeCheck(UuidPk, BidScoped, Timestamped, Base):
    __tablename__ = "scope_check"
    __table_args__ = (
        UniqueConstraint("bid_id", "system", "item_key", name="uq_scope_check"),
        CheckConstraint(f"status IN {CHECK_STATUSES}", name="status_known"),
        CheckConstraint(f"proposed_status IN {CHECK_STATUSES}", name="proposed_known"),
        CheckConstraint("decided_by IS NULL OR status <> 'open'", name="decision_resolves"),
    )

    system: Mapped[str] = mapped_column(String(24), nullable=False)
    item_key: Mapped[str] = mapped_column(String(60), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    proposed_status: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    basis: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    decided_by: Mapped[str | None] = mapped_column(
        String(200), info=personal("who resolved the checklist item")
    )
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)


class Risk(UuidPk, BidScoped, Timestamped, Base):
    __tablename__ = "risk"
    __table_args__ = (
        UniqueConstraint("bid_id", "key", name="uq_risk_key"),
        CheckConstraint(f"category IN {RISK_CATEGORIES}", name="category_known"),
        CheckConstraint(f"status IN {RISK_STATUSES}", name="status_known"),
        CheckConstraint(
            f"treatment IS NULL OR treatment IN {RISK_TREATMENTS}", name="treatment_known"
        ),
        CheckConstraint(f"impact_state IN {IMPACT_STATES}", name="impact_state_known"),
        # A risk rests on evidence, like everything else the platform proposes.
        CheckConstraint("jsonb_array_length(evidence) >= 1", name="has_evidence"),
        # FR-RSK-04: an estimator who changes the computed impact says why.
        CheckConstraint(
            "impact_state <> 'adjusted' OR btrim(coalesce(impact_reason, '')) <> ''",
            name="adjustment_reasoned",
        ),
    )

    key: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    level: Mapped[str | None] = mapped_column(String(40))
    multiplier: Mapped[str | None] = mapped_column(String(60))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    rules_version: Mapped[str] = mapped_column(String(40), nullable=False)
    last_found_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    proposed_treatment: Mapped[str] = mapped_column(String(16), nullable=False)
    treatment: Mapped[str | None] = mapped_column(String(16))
    owner: Mapped[str | None] = mapped_column(String(200), info=personal("who owns the risk"))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    note: Mapped[str | None] = mapped_column(Text)

    # What the engines worked out, and what the estimator accepted or adjusted it to.
    impact: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    impact_state: Mapped[str] = mapped_column(String(16), nullable=False, default="computed")
    cost_allowance: Mapped[Money | None] = mapped_column(MoneyType)
    programme_hours: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    impact_reason: Mapped[str | None] = mapped_column(Text)
    impact_by: Mapped[str | None] = mapped_column(
        String(200), info=personal("who accepted or adjusted the impact")
    )
    impact_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
