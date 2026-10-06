"""A view's grid marks, for checking one sheet's scale against another's (FR-VIS-05).

* `sheet_view.grid_marks`: the gridlines the view's bubbles mark, by label, across and up
  the sheet. Null where the view has none, and on views detected before this.

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sheet_view", sa.Column("grid_marks", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("sheet_view", "grid_marks")
