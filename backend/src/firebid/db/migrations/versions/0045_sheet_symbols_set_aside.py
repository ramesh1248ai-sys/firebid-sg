"""How many shapes of a sheet were not taken as candidate symbols, and why (FR-VIS-02).

* `sheet_geometry.symbols_set_aside`: counts by rule (`base_plan`, `straight`, `leader`) of
  the shapes the symbol detector set aside. Null on sheets read before this. A tender that
  prints its services in grey would lose them to the base-plan rule: the count says so.

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sheet_geometry", sa.Column("symbols_set_aside", postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("sheet_geometry", "symbols_set_aside")
