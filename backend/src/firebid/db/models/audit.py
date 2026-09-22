"""Append-only audit trail with a per-bid hash chain (NFR-09).

`audit_event` is range-partitioned by month. Rows are never updated or deleted: the app role
has no rights to do so and a trigger refuses it (see migration 0002).

Tamper evidence is one chain link per database transaction per chain, so a bulk action over
thousands of rows costs one link, and two bids never wait on each other's lock.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from firebid.db.base import Base
from firebid.db.mixins import personal


class AuditEvent(Base):
    """One recorded action: a state transition, a gate decision or a human edit."""

    __tablename__ = "audit_event"
    __table_args__ = (
        Index("ix_audit_event_bid_occurred", "bid_id", "occurred_at"),
        Index("ix_audit_event_entity", "entity_type", "entity_id"),
        Index("ix_audit_event_actor", "actor_id"),
        {"postgresql_partition_by": "RANGE (occurred_at)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now()
    )
    organisation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    bid_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    chain_key: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, info=personal("names the person who took the action, for accountability")
    )
    actor_label: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        info=personal("the actor's display name at the time, kept so history stays readable"),
    )
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(80), nullable=False)
    before: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    tx_id: Mapped[int] = mapped_column(BigInteger, nullable=False)


class AuditChainLink(Base):
    """One link per transaction per chain: hashes that transaction's events onto the chain."""

    __tablename__ = "audit_chain_link"
    __table_args__ = (UniqueConstraint("chain_key", "seq", name="uq_chain_seq"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    chain_key: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    organisation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    bid_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    events_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    tx_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IdCounter(Base):
    """Backs the human IDs (BID-2026-014, QTO-000347) with an atomic increment."""

    __tablename__ = "id_counter"

    scope: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class AuditRetention(Base):
    """Retention policy per organisation (NFR-09 default: at least 7 years)."""

    __tablename__ = "audit_retention"

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organisation.id", ondelete="CASCADE"), primary_key=True
    )
    retain_years: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
