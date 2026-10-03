"""Quotations, FX rates, the cost build-up and price history (P2-04).

* `quotation`, `quotation_line`: a supplier's quotation as captured for a bid, with the
  file it came from and the line of that file each field was read from. Bid-scoped, under
  row-level security: a quotation is a bid's commercial record.
* `fx_rate`: a recorded exchange rate to SGD, with its source and date. Organisation-level.
* `cost_buildup_line`: what an estimator entered for a component of a bid's cost build-up.
  Append-only: the latest line of a component is in force. Bid-scoped.
* `bid_price_basis`: the day a bid is priced, which fixes its GST rate. Bid-scoped.
* `erp_item`, `price_history`: the ERP's item master, and past purchase order and project
  prices that current prices are compared with. Organisation-level.

A confirmed quotation line prices nothing by itself: confirming it adds an entry to the
rate library (`rate.quotation_line_id`), and a bill is priced from the library as before.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
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
from firebid.db.mixins import BidScoped, CreatedBy, Timestamped, UuidPk, personal
from firebid.db.types import MoneyType
from firebid.domain.values import Money

QUOTATION_STATES = ("extracted", "confirmed", "rejected")
BUILDUP_BASES = ("lump_sum", "percentage")
HISTORY_KINDS = ("purchase_order", "project")


class Quotation(UuidPk, BidScoped, Timestamped, CreatedBy, Base):
    __tablename__ = "quotation"
    __table_args__ = (
        UniqueConstraint("bid_id", "sha256", name="uq_quotation_file"),
        CheckConstraint(f"state IN {QUOTATION_STATES}", name="state_known"),
        CheckConstraint("state = 'extracted' OR decided_by IS NOT NULL", name="decision_named"),
    )

    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    scan_signature: Mapped[str | None] = mapped_column(String(200))
    supplier: Mapped[str | None] = mapped_column(String(200))
    quote_number: Mapped[str | None] = mapped_column(String(120))
    quote_date: Mapped[date | None] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(String(3))
    delivery_terms: Mapped[str | None] = mapped_column(String(200))
    incoterm: Mapped[str | None] = mapped_column(String(3))
    lead_time: Mapped[str | None] = mapped_column(String(120))
    exclusions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # Each field as it was read, with the line of the file it was read from; what was not
    # stated; and how it was read (rule version, or model, prompt and agent run).
    extraction: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="extracted")
    decided_by: Mapped[str | None] = mapped_column(
        String(200), info=personal("who confirmed or rejected the quotation")
    )
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)


class QuotationLine(UuidPk, BidScoped, Timestamped, Base):
    __tablename__ = "quotation_line"
    __table_args__ = (UniqueConstraint("quotation_id", "ordinal", name="uq_quotation_line"),)

    quotation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("quotation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    brand: Mapped[str | None] = mapped_column(String(120))
    model: Mapped[str | None] = mapped_column(String(120))
    unit: Mapped[str | None] = mapped_column(String(16))
    # In the quotation's currency, exactly as written.
    unit_price: Mapped[Decimal] = mapped_column(Numeric(16, 4), nullable=False)
    moq: Mapped[str | None] = mapped_column(String(60))
    lead_time: Mapped[str | None] = mapped_column(String(120))
    source: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    # What it prices: a rate-library item key, and optionally the bill line it was for.
    item_key: Mapped[str | None] = mapped_column(String(200), index=True)
    boq_line_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("boq_line.id", ondelete="SET NULL")
    )
    # The rate-library entry made from it when the quotation was confirmed.
    rate_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("rate.id", ondelete="SET NULL")
    )
    landed: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class FxRate(UuidPk, Timestamped, CreatedBy, Base):
    __tablename__ = "fx_rate"
    __table_args__ = (
        UniqueConstraint("organisation_id", "currency", "as_of", "source", name="uq_fx_rate"),
        CheckConstraint("rate > 0", name="rate_positive"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organisation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)  # SGD per unit
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class CostBuildupLine(UuidPk, BidScoped, Timestamped, Base):
    __tablename__ = "cost_buildup_line"
    __table_args__ = (
        CheckConstraint(f"basis IN {BUILDUP_BASES}", name="basis_known"),
        # FR-CST-09: an entered cost is an estimator's, and says whose.
        CheckConstraint("entered_by <> ''", name="estimator_named"),
        CheckConstraint(
            "(basis = 'lump_sum' AND amount IS NOT NULL) OR "
            "(basis = 'percentage' AND percent IS NOT NULL AND base IS NOT NULL)",
            name="value_for_basis",
        ),
    )

    component: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    basis: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Money | None] = mapped_column(MoneyType)
    percent: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    base: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)
    entered_by: Mapped[str] = mapped_column(
        String(200), nullable=False, info=personal("the estimator whose figure it is")
    )
    entered_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BidPriceBasis(UuidPk, BidScoped, Timestamped, Base):
    __tablename__ = "bid_price_basis"
    __table_args__ = (UniqueConstraint("bid_id", name="uq_bid_price_basis"),)

    priced_on: Mapped[date] = mapped_column(Date, nullable=False)
    set_by: Mapped[str] = mapped_column(
        String(200), nullable=False, info=personal("who set the bid's pricing date")
    )


class ErpItem(UuidPk, Timestamped, Base):
    __tablename__ = "erp_item"
    __table_args__ = (UniqueConstraint("organisation_id", "code", name="uq_erp_item_code"),)

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organisation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    item_key: Mapped[str | None] = mapped_column(String(200), index=True)


class PriceHistory(UuidPk, Timestamped, Base):
    __tablename__ = "price_history"
    __table_args__ = (
        UniqueConstraint(
            "organisation_id",
            "kind",
            "reference",
            "item_key",
            "unit",
            "on_date",
            name="uq_price_history",
        ),
        CheckConstraint(f"kind IN {HISTORY_KINDS}", name="kind_known"),
        CheckConstraint("unit_price > 0", name="price_positive"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organisation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    reference: Mapped[str] = mapped_column(String(200), nullable=False)
    on_date: Mapped[date] = mapped_column(Date, nullable=False)
    item_key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(16, 4), nullable=False)  # SGD
    description: Mapped[str | None] = mapped_column(Text)
    supplier: Mapped[str | None] = mapped_column(String(200))
