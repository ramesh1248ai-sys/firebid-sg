"""The opt-in debugging payload store (NFR-14).

Prompts and responses are the tender document in a second place, so this is off by default,
encrypted, expiring, and bid-scoped like everything else that holds tender content.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BID_SCOPED = ("payload_capture", "llm_payload")


def upgrade() -> None:
    op.create_table(
        "payload_capture",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("retention_days", sa.Integer(), nullable=False, server_default="14"),
        sa.Column("enabled_by", sa.String(200), nullable=True),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["bid_id"], ["bid.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payload_capture")),
        sa.UniqueConstraint("bid_id", name="uq_payload_capture_bid"),
    )
    op.create_index("ix_payload_capture_bid_id", "payload_capture", ["bid_id"])

    op.create_table(
        "llm_payload",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("route", sa.String(80), nullable=False),
        sa.Column("prompt_encrypted", sa.Text(), nullable=False),
        sa.Column("response_encrypted", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reads", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["bid_id"], ["bid.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_run.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_payload")),
    )
    op.create_index("ix_llm_payload_bid_id", "llm_payload", ["bid_id"])
    # The retention sweep reads by expiry.
    op.create_index("ix_llm_payload_expires_at", "llm_payload", ["expires_at"])

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
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO firebid_app")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO firebid_service")


def downgrade() -> None:
    op.drop_index("ix_llm_payload_expires_at", table_name="llm_payload")
    op.drop_index("ix_llm_payload_bid_id", table_name="llm_payload")
    op.drop_table("llm_payload")
    op.drop_index("ix_payload_capture_bid_id", table_name="payload_capture")
    op.drop_table("payload_capture")
