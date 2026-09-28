"""The QTO engine (P1-07): item keys, duplicate groups, bid parameters, rule status.

* `qto_item` gains a stable key, an inputs hash and its derivation, and a new version keeps
  its item's human ID (the uniqueness is per version).
* `duplicate_group` (FR-QTO-08) and `bid_parameter` (rule inputs) are tender records, under
  row-level security per bid.
* `measurement_rule.status` says whether a rule is a seeded default "to be confirmed".

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BID_SCOPED = ("duplicate_group", "bid_parameter")


def upgrade() -> None:
    op.add_column("qto_item", sa.Column("item_key", sa.String(64), nullable=True))
    op.add_column("qto_item", sa.Column("inputs_hash", sa.String(64), nullable=True))
    op.add_column(
        "qto_item",
        sa.Column("derivation", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_index(op.f("ix_qto_item_item_key"), "qto_item", ["item_key"])
    op.drop_constraint("uq_qto_item_human_id", "qto_item", type_="unique")
    op.create_unique_constraint(
        "uq_qto_item_human_id", "qto_item", ["bid_id", "human_id", "version"]
    )

    op.add_column(
        "measurement_rule",
        sa.Column("status", sa.String(40), nullable=False, server_default="to be confirmed"),
    )

    op.create_table(
        "duplicate_group",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("level", sa.String(40), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("members", postgresql.JSONB(), nullable=False),
        sa.Column("decided_by_id", sa.Uuid(), nullable=True),
        sa.Column("decided_by", sa.String(200), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_duplicate_group_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_id"],
            ["app_user.id"],
            name=op.f("fk_duplicate_group_decided_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_duplicate_group")),
        sa.UniqueConstraint("bid_id", "key", name="uq_duplicate_group_key"),
        sa.CheckConstraint(
            "kind IN ('enlarged_plan', 'match_line', 'schematic')",
            name=op.f("ck_duplicate_group_kind_known"),
        ),
        sa.CheckConstraint(
            "status IN ('unresolved', 'auto_excluded', 'confirmed', 'not_duplicate')",
            name=op.f("ck_duplicate_group_status_known"),
        ),
    )
    op.create_index(op.f("ix_duplicate_group_bid_id"), "duplicate_group", ["bid_id"])

    op.create_table(
        "bid_parameter",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("level", sa.String(40), nullable=True),
        sa.Column("value", sa.Numeric(14, 3), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_bid_parameter_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f("fk_bid_parameter_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bid_parameter")),
    )
    op.create_index(op.f("ix_bid_parameter_bid_id"), "bid_parameter", ["bid_id"])

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
    op.drop_table("bid_parameter")
    op.drop_table("duplicate_group")
    op.drop_column("measurement_rule", "status")
    op.drop_constraint("uq_qto_item_human_id", "qto_item", type_="unique")
    op.create_unique_constraint("uq_qto_item_human_id", "qto_item", ["bid_id", "human_id"])
    op.drop_index(op.f("ix_qto_item_item_key"), table_name="qto_item")
    op.drop_column("qto_item", "derivation")
    op.drop_column("qto_item", "inputs_hash")
    op.drop_column("qto_item", "item_key")
