"""Deadline alert intervals per organisation, and a record of what has been sent.

FR-BID-03. The record makes the job idempotent: each deadline and interval notifies once,
however often the job runs.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEADLINE_KINDS = ("submission", "clarification_cutoff")


def upgrade() -> None:
    op.add_column(
        "organisation",
        sa.Column(
            "deadline_alert_days",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[7, 3, 1]'::jsonb"),
        ),
    )
    op.create_table(
        "deadline_alert",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("deadline_kind", sa.String(32), nullable=False),
        sa.Column("days_before", sa.Integer(), nullable=False),
        sa.Column("recipient_count", sa.Integer(), nullable=False),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["bid_id"], ["bid.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deadline_alert")),
        sa.UniqueConstraint(
            "bid_id", "deadline_kind", "days_before", name="uq_deadline_alert_once"
        ),
        sa.CheckConstraint(
            f"deadline_kind IN {DEADLINE_KINDS}", name="ck_deadline_alert_kind_known"
        ),
    )
    op.create_index("ix_deadline_alert_bid_id", "deadline_alert", ["bid_id"])

    # New bid-scoped table, so it needs the same policies as the rest (migration 0003).
    op.execute("ALTER TABLE deadline_alert ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY firebid_app_bid_scope ON deadline_alert FOR ALL TO firebid_app "
        "USING (firebid_can_see_bid(bid_id)) WITH CHECK (firebid_can_see_bid(bid_id))"
    )
    op.execute(
        "CREATE POLICY firebid_service_all ON deadline_alert FOR ALL TO firebid_service "
        "USING (true) WITH CHECK (true)"
    )


def downgrade() -> None:
    op.drop_index("ix_deadline_alert_bid_id", table_name="deadline_alert")
    op.drop_table("deadline_alert")
    op.drop_column("organisation", "deadline_alert_days")
