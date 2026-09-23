"""What a bid may spend on AI processing (NFR-15).

Bid-scoped, so it carries the same row-level security as the rest of a bid's data: only the
bid's team can see or change its budget.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bid_budget",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("limit_sgd", sa.Numeric(12, 2), nullable=True),
        sa.Column("alert_at", postgresql.JSONB(), nullable=False, server_default="[0.5, 0.8, 1.0]"),
        sa.Column("alerts_sent", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("owner_email", sa.String(320), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["bid_id"], ["bid.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bid_budget")),
        sa.UniqueConstraint("bid_id", name="uq_bid_budget_bid"),
    )
    op.create_index("ix_bid_budget_bid_id", "bid_budget", ["bid_id"])

    # A new bid-scoped table needs the same policies as the rest (migrations 0003 and 0007).
    op.execute("ALTER TABLE bid_budget ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY firebid_app_bid_scope ON bid_budget FOR ALL TO firebid_app "
        "USING (firebid_can_see_bid(bid_id)) WITH CHECK (firebid_can_see_bid(bid_id))"
    )
    op.execute(
        "CREATE POLICY firebid_service_all ON bid_budget FOR ALL TO firebid_service "
        "USING (true) WITH CHECK (true)"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON bid_budget TO firebid_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON bid_budget TO firebid_service")


def downgrade() -> None:
    op.drop_index("ix_bid_budget_bid_id", table_name="bid_budget")
    op.drop_table("bid_budget")
