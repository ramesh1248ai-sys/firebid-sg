"""Supplier quotations and the cost build-up (P2-04).

* `quotation`, `quotation_line`, `cost_buildup_line`, `bid_price_basis`: bid-scoped, under
  row-level security.
* `fx_rate`, `erp_item`, `price_history`: organisation-level, like the rate library.
* `rate.quotation_line_id`, `rate.landed`: the quotation line a rate was made from, and how
  its price was brought to SGD.
* `boq_line.allowance_by`: the estimator whose allowance an amount with no rate entry is.
  Allowances entered before this step carry no name, and G2 asks for one.

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-03
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMPTY = sa.text("'{}'::jsonb")
EMPTY_LIST = sa.text("'[]'::jsonb")
BID_SCOPED = ("quotation", "quotation_line", "cost_buildup_line", "bid_price_basis")


def _created() -> sa.Column[datetime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _id() -> sa.Column[Any]:
    return sa.Column("id", sa.Uuid(), nullable=False)


def _bid(table: str) -> list[Any]:
    return [
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f(f"fk_{table}_bid_id_bid"), ondelete="CASCADE"
        ),
    ]


def _organisation(table: str) -> list[Any]:
    return [
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f(f"fk_{table}_organisation_id_organisation"),
            ondelete="CASCADE",
        ),
    ]


def _created_by(table: str) -> list[Any]:
    return [
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f(f"fk_{table}_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
    ]


def upgrade() -> None:
    op.add_column("rate", sa.Column("quotation_line_id", sa.Uuid(), nullable=True))
    op.add_column("rate", sa.Column("landed", postgresql.JSONB(), nullable=True))
    op.create_index(op.f("ix_rate_quotation_line_id"), "rate", ["quotation_line_id"])
    op.add_column("boq_line", sa.Column("allowance_by", sa.String(200), nullable=True))
    op.add_column("boq_line", sa.Column("allowance_by_id", sa.Uuid(), nullable=True))

    op.create_table(
        "quotation",
        _id(),
        *_bid("quotation"),
        sa.Column("filename", sa.String(300), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("scan_signature", sa.String(200), nullable=True),
        sa.Column("supplier", sa.String(200), nullable=True),
        sa.Column("quote_number", sa.String(120), nullable=True),
        sa.Column("quote_date", sa.Date(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("delivery_terms", sa.String(200), nullable=True),
        sa.Column("incoterm", sa.String(3), nullable=True),
        sa.Column("lead_time", sa.String(120), nullable=True),
        sa.Column("exclusions", postgresql.JSONB(), nullable=False, server_default=EMPTY_LIST),
        sa.Column("extraction", postgresql.JSONB(), nullable=False, server_default=EMPTY),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("decided_by", sa.String(200), nullable=True),
        sa.Column("decided_by_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        *_created_by("quotation"),
        _created(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quotation")),
        sa.UniqueConstraint("bid_id", "sha256", name=op.f("uq_quotation_file")),
        sa.CheckConstraint(
            "state IN ('extracted', 'confirmed', 'rejected')",
            name=op.f("ck_quotation_state_known"),
        ),
        sa.CheckConstraint(
            "state = 'extracted' OR decided_by IS NOT NULL",
            name=op.f("ck_quotation_decision_named"),
        ),
    )
    op.create_index(op.f("ix_quotation_bid_id"), "quotation", ["bid_id"])

    op.create_table(
        "quotation_line",
        _id(),
        *_bid("quotation_line"),
        sa.Column("quotation_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("brand", sa.String(120), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("unit", sa.String(16), nullable=True),
        sa.Column("unit_price", sa.Numeric(16, 4), nullable=False),
        sa.Column("moq", sa.String(60), nullable=True),
        sa.Column("lead_time", sa.String(120), nullable=True),
        sa.Column("source", postgresql.JSONB(), nullable=False, server_default=EMPTY),
        sa.Column("item_key", sa.String(200), nullable=True),
        sa.Column("boq_line_id", sa.Uuid(), nullable=True),
        sa.Column("rate_id", sa.Uuid(), nullable=True),
        sa.Column("landed", postgresql.JSONB(), nullable=True),
        _created(),
        sa.ForeignKeyConstraint(
            ["quotation_id"],
            ["quotation.id"],
            name=op.f("fk_quotation_line_quotation_id_quotation"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["boq_line_id"],
            ["boq_line.id"],
            name=op.f("fk_quotation_line_boq_line_id_boq_line"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["rate_id"],
            ["rate.id"],
            name=op.f("fk_quotation_line_rate_id_rate"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quotation_line")),
        sa.UniqueConstraint("quotation_id", "ordinal", name=op.f("uq_quotation_line")),
    )
    op.create_index(op.f("ix_quotation_line_bid_id"), "quotation_line", ["bid_id"])
    op.create_index(op.f("ix_quotation_line_quotation_id"), "quotation_line", ["quotation_id"])
    op.create_index(op.f("ix_quotation_line_item_key"), "quotation_line", ["item_key"])

    op.create_table(
        "fx_rate",
        _id(),
        *_organisation("fx_rate"),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(14, 6), nullable=False),
        sa.Column("source", sa.String(200), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        *_created_by("fx_rate"),
        _created(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fx_rate")),
        sa.UniqueConstraint(
            "organisation_id", "currency", "as_of", "source", name=op.f("uq_fx_rate")
        ),
        sa.CheckConstraint("rate > 0", name=op.f("ck_fx_rate_rate_positive")),
    )
    op.create_index(op.f("ix_fx_rate_organisation_id"), "fx_rate", ["organisation_id"])

    op.create_table(
        "cost_buildup_line",
        _id(),
        *_bid("cost_buildup_line"),
        sa.Column("component", sa.String(40), nullable=False),
        sa.Column("basis", sa.String(16), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("percent", sa.Numeric(7, 3), nullable=True),
        sa.Column("base", sa.String(32), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("entered_by", sa.String(200), nullable=False),
        sa.Column("entered_by_id", sa.Uuid(), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        _created(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cost_buildup_line")),
        sa.CheckConstraint(
            "basis IN ('lump_sum', 'percentage')", name=op.f("ck_cost_buildup_line_basis_known")
        ),
        sa.CheckConstraint("entered_by <> ''", name=op.f("ck_cost_buildup_line_estimator_named")),
        sa.CheckConstraint(
            "(basis = 'lump_sum' AND amount IS NOT NULL) OR "
            "(basis = 'percentage' AND percent IS NOT NULL AND base IS NOT NULL)",
            name=op.f("ck_cost_buildup_line_value_for_basis"),
        ),
    )
    op.create_index(op.f("ix_cost_buildup_line_bid_id"), "cost_buildup_line", ["bid_id"])
    op.create_index(op.f("ix_cost_buildup_line_component"), "cost_buildup_line", ["component"])

    op.create_table(
        "bid_price_basis",
        _id(),
        *_bid("bid_price_basis"),
        sa.Column("priced_on", sa.Date(), nullable=False),
        sa.Column("set_by", sa.String(200), nullable=False),
        _created(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bid_price_basis")),
        sa.UniqueConstraint("bid_id", name=op.f("uq_bid_price_basis")),
    )
    op.create_index(op.f("ix_bid_price_basis_bid_id"), "bid_price_basis", ["bid_id"])

    op.create_table(
        "erp_item",
        _id(),
        *_organisation("erp_item"),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("item_key", sa.String(200), nullable=True),
        _created(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_erp_item")),
        sa.UniqueConstraint("organisation_id", "code", name=op.f("uq_erp_item_code")),
    )
    op.create_index(op.f("ix_erp_item_organisation_id"), "erp_item", ["organisation_id"])
    op.create_index(op.f("ix_erp_item_item_key"), "erp_item", ["item_key"])

    op.create_table(
        "price_history",
        _id(),
        *_organisation("price_history"),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("reference", sa.String(200), nullable=False),
        sa.Column("on_date", sa.Date(), nullable=False),
        sa.Column("item_key", sa.String(200), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("unit_price", sa.Numeric(16, 4), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("supplier", sa.String(200), nullable=True),
        _created(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_price_history")),
        sa.UniqueConstraint(
            "organisation_id",
            "kind",
            "reference",
            "item_key",
            "unit",
            "on_date",
            name=op.f("uq_price_history"),
        ),
        sa.CheckConstraint(
            "kind IN ('purchase_order', 'project')", name=op.f("ck_price_history_kind_known")
        ),
        sa.CheckConstraint("unit_price > 0", name=op.f("ck_price_history_price_positive")),
    )
    op.create_index(op.f("ix_price_history_organisation_id"), "price_history", ["organisation_id"])
    op.create_index(op.f("ix_price_history_item_key"), "price_history", ["item_key"])

    for table in BID_SCOPED:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY firebid_app_bid_scope ON {table} FOR ALL TO firebid_app "
            "USING (firebid_can_see_bid(bid_id)) WITH CHECK (firebid_can_see_bid(bid_id))"
        )
        op.execute(
            f"CREATE POLICY firebid_service_all ON {table} FOR ALL TO firebid_service "
            "USING (true) WITH CHECK (true)"
        )


def downgrade() -> None:
    for table in (
        "price_history",
        "erp_item",
        "bid_price_basis",
        "cost_buildup_line",
        "fx_rate",
        "quotation_line",
        "quotation",
    ):
        op.drop_table(table)
    op.drop_column("boq_line", "allowance_by_id")
    op.drop_column("boq_line", "allowance_by")
    op.drop_index(op.f("ix_rate_quotation_line_id"), table_name="rate")
    op.drop_column("rate", "landed")
    op.drop_column("rate", "quotation_line_id")
