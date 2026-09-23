"""The AI gateway's response cache and shared rate-limit buckets.

Both are infrastructure rather than bid data. The cache records the data class it holds so
retention can differ per class; the rate buckets hold counts only and never any content.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DATA_CLASSES = ("internal", "confidential", "commercial", "personal")


def upgrade() -> None:
    op.create_table(
        "llm_response_cache",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("route", sa.String(80), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=True),
        sa.Column("config_version", sa.String(64), nullable=True),
        sa.Column("data_class", sa.String(16), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("body_ref", sa.String(500), nullable=True),
        sa.Column("stop_reason", sa.String(20), nullable=False),
        sa.Column("tool_calls", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_response_cache")),
        sa.UniqueConstraint("cache_key", name="uq_llm_response_cache_key"),
        sa.CheckConstraint(f"data_class IN {DATA_CLASSES}", name="ck_llm_cache_data_class"),
    )
    op.create_index("ix_llm_response_cache_route", "llm_response_cache", ["route"])
    op.create_index("ix_llm_response_cache_data_class", "llm_response_cache", ["data_class"])
    # Retention sweeps read by expiry, so give them an index to work from.
    op.create_index("ix_llm_response_cache_expires_at", "llm_response_cache", ["expires_at"])

    op.create_table(
        "llm_rate_bucket",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bucket_key", sa.String(120), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_rate_bucket")),
        sa.UniqueConstraint("bucket_key", "window_start", name="uq_llm_rate_window"),
    )
    op.create_index("ix_llm_rate_bucket_window", "llm_rate_bucket", ["window_start"])

    # The application role uses both; neither is bid-scoped, so neither carries an RLS policy.
    for table in ("llm_response_cache", "llm_rate_bucket"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO firebid_app")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO firebid_service")


def downgrade() -> None:
    op.drop_index("ix_llm_rate_bucket_window", table_name="llm_rate_bucket")
    op.drop_table("llm_rate_bucket")
    op.drop_index("ix_llm_response_cache_expires_at", table_name="llm_response_cache")
    op.drop_index("ix_llm_response_cache_data_class", table_name="llm_response_cache")
    op.drop_index("ix_llm_response_cache_route", table_name="llm_response_cache")
    op.drop_table("llm_response_cache")
