"""A sheet's layout exports, made by a job and kept (FR-DSN-05).

* `sheet_design.exports`: for each format, whether its file is asked for, made or failed,
  where it is kept, and the layout it was made from.

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sheet_design",
        sa.Column(
            "exports",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("sheet_design", "exports")
