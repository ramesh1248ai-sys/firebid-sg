"""Time on task for the QTO effort measure (P1-11, requirements §14).

`activity_minute`: a minute a person spent working in the workbench on a bid, by area. One
row per bid, person, minute and area, however many heartbeats arrive in it. Bid-scoped,
under row-level security.

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "activity_minute",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("minute", sa.DateTime(timezone=True), nullable=False),
        sa.Column("area", sa.String(16), nullable=False),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_activity_minute_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_activity_minute_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activity_minute")),
        sa.UniqueConstraint("bid_id", "user_id", "minute", "area", name="uq_activity_minute"),
        sa.CheckConstraint(
            "area IN ('review', 'manual', 'duplicates', 'symbols')",
            name=op.f("ck_activity_minute_area_known"),
        ),
    )
    op.create_index(op.f("ix_activity_minute_bid_id"), "activity_minute", ["bid_id"])
    op.execute("ALTER TABLE activity_minute ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY firebid_app_bid_scope ON activity_minute FOR ALL TO firebid_app "
        "USING (firebid_can_see_bid(bid_id)) WITH CHECK (firebid_can_see_bid(bid_id))"
    )
    op.execute(
        "CREATE POLICY firebid_service_all ON activity_minute FOR ALL TO firebid_service "
        "USING (true) WITH CHECK (true)"
    )


def downgrade() -> None:
    op.drop_table("activity_minute")
