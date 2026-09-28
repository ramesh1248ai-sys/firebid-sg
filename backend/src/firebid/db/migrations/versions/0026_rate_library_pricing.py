"""Rate library pricing (P1-10, FR-CST-01).

* `rate`: the item key's parts, when an entry was retired by a newer version, and one
  current version per item, unit and source.
* `boq_line`: the line's item key, the entry a model or rule proposed, how and by whom it
  was priced.
* Provenance, in the database as well as the domain: a BOQ line's rate or amount needs a
  rate entry behind it (a provisional or lump sum's amount is the estimator's own
  allowance), and the rate and amount must be that entry's rate and the quantity times it.

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRICED_FROM_RATE = """
CREATE OR REPLACE FUNCTION firebid_price_from_rate() RETURNS trigger AS $$
DECLARE
    entry numeric(14, 2);
BEGIN
    IF NEW.rate_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT unit_rate INTO entry FROM rate WHERE id = NEW.rate_id;
    IF NEW.unit_rate IS DISTINCT FROM entry THEN
        RAISE EXCEPTION 'a BOQ line is priced at its rate entry''s rate (%), not %',
            entry, NEW.unit_rate USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.amount IS DISTINCT FROM round(NEW.quantity * entry, 2) THEN
        RAISE EXCEPTION 'a BOQ line''s amount is its quantity times its rate (%), not %',
            round(NEW.quantity * entry, 2), NEW.amount USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.add_column(
        "rate",
        sa.Column("key_parts", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column("rate", sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "uq_rate_current",
        "rate",
        ["organisation_id", "item_key", "unit", "source_type", "source_reference"],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )

    op.add_column("boq_line", sa.Column("item_key", sa.String(200), nullable=True))
    op.add_column("boq_line", sa.Column("proposed_rate_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_boq_line_proposed_rate_id_rate"),
        "boq_line",
        "rate",
        ["proposed_rate_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("boq_line", sa.Column("price_method", sa.String(16), nullable=True))
    op.add_column("boq_line", sa.Column("price_reason", sa.Text(), nullable=True))
    op.add_column(
        "boq_line",
        sa.Column(
            "price_provenance", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")
        ),
    )
    op.add_column("boq_line", sa.Column("priced_by_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_boq_line_priced_by_id_app_user"),
        "boq_line",
        "app_user",
        ["priced_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("boq_line", sa.Column("priced_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_boq_line_item_key"), "boq_line", ["item_key"])
    op.create_check_constraint(
        op.f("ck_boq_line_price_method_known"),
        "boq_line",
        "price_method IS NULL OR price_method IN ('rule', 'model', 'person')",
    )
    op.create_check_constraint(
        op.f("ck_boq_line_priced_from_rate"),
        "boq_line",
        "rate_id IS NOT NULL OR (unit_rate IS NULL AND "
        "(amount IS NULL OR is_provisional OR is_lump_sum))",
    )
    op.execute(PRICED_FROM_RATE)
    op.execute(
        "CREATE TRIGGER boq_line_price_from_rate BEFORE INSERT OR UPDATE ON boq_line "
        "FOR EACH ROW EXECUTE FUNCTION firebid_price_from_rate()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS boq_line_price_from_rate ON boq_line")
    op.execute("DROP FUNCTION IF EXISTS firebid_price_from_rate()")
    op.drop_constraint(op.f("ck_boq_line_priced_from_rate"), "boq_line", type_="check")
    op.drop_constraint(op.f("ck_boq_line_price_method_known"), "boq_line", type_="check")
    op.drop_index(op.f("ix_boq_line_item_key"), table_name="boq_line")
    op.drop_constraint(op.f("fk_boq_line_priced_by_id_app_user"), "boq_line", type_="foreignkey")
    op.drop_constraint(op.f("fk_boq_line_proposed_rate_id_rate"), "boq_line", type_="foreignkey")
    for column in (
        "priced_at",
        "priced_by_id",
        "price_provenance",
        "price_reason",
        "price_method",
        "proposed_rate_id",
        "item_key",
    ):
        op.drop_column("boq_line", column)
    op.drop_index("uq_rate_current", table_name="rate")
    op.drop_column("rate", "retired_at")
    op.drop_column("rate", "key_parts")
