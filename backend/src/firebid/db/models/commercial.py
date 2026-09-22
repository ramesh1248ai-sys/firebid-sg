"""BOQs, the client's own BOQ, their mapping, and the rate library."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from firebid.db.base import Base
from firebid.db.mixins import BidScoped, CreatedBy, Timestamped, UuidPk
from firebid.db.types import MoneyType
from firebid.domain.values import Money

BOQ_KINDS = ("company", "client_priced")
RATE_SOURCES = ("company_standard", "purchase_order", "quotation")


class Boq(UuidPk, BidScoped, Timestamped, CreatedBy, Base):
    """A bill of quantities the platform generates from verified quantities."""

    __tablename__ = "boq"
    __table_args__ = (CheckConstraint(f"kind IN {BOQ_KINDS}", name="kind_known"),)

    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="company")
    template_key: Mapped[str | None] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class BoqLine(UuidPk, BidScoped, Timestamped, Base):
    """A priced line. Lines without a trace to evidence must be marked (FR-BOQ-05)."""

    __tablename__ = "boq_line"

    boq_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("boq.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section: Mapped[str | None] = mapped_column(String(200))
    item_no: Mapped[str | None] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(16, 3), nullable=False)
    unit_rate: Mapped[Money | None] = mapped_column(MoneyType)
    amount: Mapped[Money | None] = mapped_column(MoneyType)
    rate_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("rate.id", ondelete="RESTRICT"), index=True
    )
    is_provisional: Mapped[bool] = mapped_column(default=False, nullable=False)
    is_lump_sum: Mapped[bool] = mapped_column(default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class BoqLineSource(Base):
    """Which QTO items a BOQ line is built from: the trace behind every quantity."""

    __tablename__ = "boq_line_source"

    boq_line_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("boq_line.id", ondelete="CASCADE"), primary_key=True
    )
    qto_item_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    bid_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("bid.id", ondelete="CASCADE"), nullable=False, index=True
    )


class ClientBoq(UuidPk, BidScoped, Timestamped, CreatedBy, Base):
    """The client's own pricing schedule, imported from their workbook."""

    __tablename__ = "client_boq"

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sheet_name: Mapped[str | None] = mapped_column(String(120))
    header_row: Mapped[int | None] = mapped_column(Integer)
    column_map: Mapped[dict[str, int] | None] = mapped_column(JSONB)


class ClientBoqLine(UuidPk, BidScoped, Timestamped, Base):
    """One row of the client's workbook, kept with its position so exports can patch it."""

    __tablename__ = "client_boq_line"

    client_boq_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("client_boq.id", ondelete="CASCADE"), nullable=False, index=True
    )
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(String(200))
    item_no: Mapped[str | None] = mapped_column(String(40))
    description: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(16))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(16, 3))
    unit_rate: Mapped[Money | None] = mapped_column(MoneyType)
    amount_is_formula: Mapped[bool] = mapped_column(default=False, nullable=False)


class ClientBoqMapping(UuidPk, BidScoped, Timestamped, Base):
    """How a client line maps to our measured quantities, and the variance between them."""

    __tablename__ = "client_boq_mapping"
    __table_args__ = (UniqueConstraint("client_boq_line_id", name="uq_mapping_client_line"),)

    client_boq_line_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("client_boq_line.id", ondelete="CASCADE"), nullable=False
    )
    boq_line_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("boq_line.id", ondelete="SET NULL"), index=True
    )
    measured_quantity: Mapped[Decimal | None] = mapped_column(Numeric(16, 3))
    variance_percent: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    confirmed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="SET NULL")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Rate(UuidPk, Timestamped, CreatedBy, Base):
    """A unit rate with its source, date and validity. No price exists without one."""

    __tablename__ = "rate"
    __table_args__ = (CheckConstraint(f"source_type IN {RATE_SOURCES}", name="source_known"),)

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organisation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    unit_rate: Mapped[Money] = mapped_column(MoneyType, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="SGD")
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(200), nullable=False)
    effective_from: Mapped[date] = mapped_column(nullable=False)
    valid_until: Mapped[date | None] = mapped_column()
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("rate.id", ondelete="SET NULL")
    )
