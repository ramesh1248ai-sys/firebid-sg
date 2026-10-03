"""Gate approvals, human tasks and agent runs."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from firebid.db.base import Base
from firebid.db.mixins import BidScoped, Timestamped, UuidPk

GATES = ("G0", "G1", "G2", "G3", "G4")
GATE_DECISIONS = ("approved", "refused")
TASK_STATES = ("open", "in_progress", "done", "cancelled")
RUN_STATES = ("running", "succeeded", "failed", "escalated", "cancelled")


class Approval(UuidPk, BidScoped, Timestamped, Base):
    """A recorded gate decision: who approved what, and the state they approved."""

    __tablename__ = "approval"
    __table_args__ = (
        CheckConstraint(f"gate IN {GATES}", name="gate_known"),
        CheckConstraint(f"decision IN {GATE_DECISIONS}", name="decision_known"),
    )

    gate: Mapped[str] = mapped_column(String(4), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    approver_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    approver_role: Mapped[str] = mapped_column(String(40), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    # P2-02: a later change to what was approved reopened the gate. A reopened approval no
    # longer counts as passed; the items that reopened it are what a person looks at again.
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_reason: Mapped[str | None] = mapped_column(Text)
    reopened_items: Mapped[list[str] | None] = mapped_column(JSONB)


class HumanTask(UuidPk, Timestamped, Base):
    """Work that needs a person: a review queue item, or an agent's escalation."""

    __tablename__ = "human_task"
    __table_args__ = (CheckConstraint(f"state IN {TASK_STATES}", name="state_known"),)

    bid_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("bid.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="open", index=True)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="SET NULL"), index=True
    )
    required_role: Mapped[str | None] = mapped_column(String(40))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    continuation_task: Mapped[str | None] = mapped_column(String(120))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="SET NULL")
    )


class AgentRun(UuidPk, Timestamped, Base):
    """One agent or model call: provenance, cost and outcome (NFR-11, NFR-14, NFR-15)."""

    __tablename__ = "agent_run"
    __table_args__ = (CheckConstraint(f"state IN {RUN_STATES}", name="state_known"),)

    bid_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("bid.id", ondelete="CASCADE"), index=True
    )
    route: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    agent: Mapped[str | None] = mapped_column(String(80))
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(80))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    config_version: Mapped[str | None] = mapped_column(String(64))
    input_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost_sgd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cache_hit: Mapped[bool] = mapped_column(default=False, nullable=False)
    emulated_capabilities: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(80))
    output_ref: Mapped[str | None] = mapped_column(String(512))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeadlineAlert(UuidPk, BidScoped, Base):
    """One alert per bid, deadline and interval, so a repeated run never notifies twice."""

    __tablename__ = "deadline_alert"
    __table_args__ = (
        UniqueConstraint("bid_id", "deadline_kind", "days_before", name="uq_deadline_alert_once"),
        CheckConstraint(
            "deadline_kind IN ('submission', 'clarification_cutoff')",
            name="ck_deadline_alert_kind_known",
        ),
    )

    deadline_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    days_before: Mapped[int] = mapped_column(Integer, nullable=False)
    recipient_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
