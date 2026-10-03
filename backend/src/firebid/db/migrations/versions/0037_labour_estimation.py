"""Labour estimation (P2-05).

* `labour_productivity`: the organisation's productivity library, versioned.
* `labour_condition`: multipliers proposed or confirmed for a bid or a level, under
  row-level security.

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "labour_productivity",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("item_type", sa.String(80), nullable=False),
        sa.Column("dn", sa.String(40), nullable=False, server_default=""),
        sa.Column("joining", sa.String(60), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("hours_per_unit", sa.Numeric(10, 4), nullable=False),
        sa.Column("trade", sa.String(40), nullable=False),
        sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column("source_reference", sa.String(200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_labour_productivity_organisation_id_organisation"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["labour_productivity.id"],
            name=op.f("fk_labour_productivity_supersedes_id_labour_productivity"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f("fk_labour_productivity_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_labour_productivity")),
        sa.CheckConstraint(
            "source_type IN ('company_standard', 'historical_project', 'estimator_judgement')",
            name=op.f("ck_labour_productivity_source_known"),
        ),
        sa.CheckConstraint(
            "btrim(source_reference) <> ''", name=op.f("ck_labour_productivity_source_named")
        ),
        sa.CheckConstraint(
            "hours_per_unit > 0", name=op.f("ck_labour_productivity_hours_positive")
        ),
    )
    op.create_index(
        op.f("ix_labour_productivity_organisation_id"), "labour_productivity", ["organisation_id"]
    )
    op.create_index(
        "uq_labour_productivity_current",
        "labour_productivity",
        ["organisation_id", "item_type", "dn", "joining", "unit"],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )

    op.create_table(
        "labour_condition",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("level", sa.String(40), nullable=True),
        sa.Column("multiplier_key", sa.String(60), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("basis", sa.Text(), nullable=False, server_default=""),
        sa.Column("proposed_by", sa.String(200), nullable=False),
        sa.Column("decided_by", sa.String(200), nullable=True),
        sa.Column("decided_by_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"],
            ["bid.id"],
            name=op.f("fk_labour_condition_bid_id_bid"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_labour_condition")),
        sa.CheckConstraint(
            "state IN ('proposed', 'confirmed', 'rejected')",
            name=op.f("ck_labour_condition_state_known"),
        ),
        sa.CheckConstraint(
            "state = 'proposed' OR decided_by IS NOT NULL",
            name=op.f("ck_labour_condition_decision_named"),
        ),
    )
    op.create_index(op.f("ix_labour_condition_bid_id"), "labour_condition", ["bid_id"])
    op.create_index(
        "uq_labour_condition",
        "labour_condition",
        ["bid_id", sa.text("coalesce(level, '')"), "multiplier_key"],
        unique=True,
    )
    op.execute("ALTER TABLE labour_condition ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY firebid_app_bid_scope ON labour_condition FOR ALL TO firebid_app "
        "USING (firebid_can_see_bid(bid_id)) WITH CHECK (firebid_can_see_bid(bid_id))"
    )
    op.execute(
        "CREATE POLICY firebid_service_all ON labour_condition FOR ALL TO firebid_service "
        "USING (true) WITH CHECK (true)"
    )


def downgrade() -> None:
    op.drop_table("labour_condition")
    op.drop_table("labour_productivity")
