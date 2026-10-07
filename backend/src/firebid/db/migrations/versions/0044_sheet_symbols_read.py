"""Which detector read a sheet's symbols, and whether they are matched (FR-VIS-02).

* `sheet_geometry.symbols_version`: the symbol detector that last read the sheet. An
  instance carries its detector's version too, but a sheet with no symbols has no instance
  to carry it. Null on sheets read before this.
* `sheet_geometry.symbols_matched`: false from when a sheet's symbols are read until the
  bid's symbols are next matched. A document that finishes with nothing new to match does
  not match the whole bid again.

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sheet_geometry", sa.Column("symbols_version", sa.String(16), nullable=True))
    op.add_column(
        "sheet_geometry",
        sa.Column("symbols_matched", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("sheet_geometry", "symbols_matched")
    op.drop_column("sheet_geometry", "symbols_version")
