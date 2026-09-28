"""The verification workbench (P1-08): review actions and correction events.

Both are tender records, under row-level security per bid.

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BID_SCOPED = ("review_action", "correction_event")


def upgrade() -> None:
    op.create_table(
        "review_action",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("actor_label", sa.String(200), nullable=False),
        sa.Column("reason_code", sa.String(40), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("entries", postgresql.JSONB(), nullable=False),
        sa.Column("undoes_id", sa.Uuid(), nullable=True),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_review_action_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["app_user.id"],
            name=op.f("fk_review_action_actor_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["undoes_id"],
            ["review_action.id"],
            name=op.f("fk_review_action_undoes_id_review_action"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_action")),
        sa.CheckConstraint(
            "kind IN ('accept', 'edit', 'reject', 'reject_detections', 'undo')",
            name=op.f("ck_review_action_kind_known"),
        ),
    )
    op.create_index(op.f("ix_review_action_bid_id"), "review_action", ["bid_id"])

    op.create_table(
        "correction_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("action_id", sa.Uuid(), nullable=True),
        sa.Column("qto_item_id", sa.Uuid(), nullable=True),
        sa.Column("detection_id", sa.Uuid(), nullable=True),
        sa.Column("object_type", sa.String(80), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=False),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column("reason_code", sa.String(40), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("detector_method", sa.String(40), nullable=True),
        sa.Column("detector_version", sa.String(40), nullable=True),
        sa.Column("calibration_version", sa.String(40), nullable=True),
        sa.Column("rule_version", sa.String(200), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("consultant_key", sa.String(120), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("data_policy", sa.String(40), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"],
            ["bid.id"],
            name=op.f("fk_correction_event_bid_id_bid"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["action_id"],
            ["review_action.id"],
            name=op.f("fk_correction_event_action_id_review_action"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["app_user.id"],
            name=op.f("fk_correction_event_actor_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_correction_event")),
        sa.CheckConstraint(
            "kind IN ('edit', 'reject', 'false_detection')",
            name=op.f("ck_correction_event_kind_known"),
        ),
    )
    op.create_index(op.f("ix_correction_event_bid_id"), "correction_event", ["bid_id"])
    op.create_index(op.f("ix_correction_event_qto_item_id"), "correction_event", ["qto_item_id"])

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
    op.drop_table("correction_event")
    op.drop_table("review_action")
