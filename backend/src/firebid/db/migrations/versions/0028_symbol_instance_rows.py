"""Which primitives each symbol instance is made of (P1-11, NFR-01).

`symbol_instance.geometry_rows`: the rows of the sheet's geometry that the symbol reader
grouped into this instance. Detection needs them to keep a symbol's strokes out of pipe
tracing. Recording them once means detection no longer finds every symbol on the sheet a
second time. Null on instances read before this column: detection finds those as before.

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "symbol_instance",
        sa.Column("geometry_rows", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("symbol_instance", "geometry_rows")
