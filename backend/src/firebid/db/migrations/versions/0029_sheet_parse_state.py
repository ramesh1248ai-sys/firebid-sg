"""Each sheet's own parse outcome, for the staged pipeline (ADR-010).

`sheet.parsed_at` is when the sheet's `parse.sheet` job finished; `sheet.parse_error` is why
it failed, if it did. A failed sheet is finished too, so its document can finish. Sheets
parsed before this migration were parsed whole, in one job: they are marked parsed now.

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sheet", sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sheet", sa.Column("parse_error", sa.Text(), nullable=True))
    op.execute(
        "UPDATE sheet SET parsed_at = sheet.created_at FROM document "
        "WHERE document.id = sheet.document_id AND document.state = 'done'"
    )


def downgrade() -> None:
    op.drop_column("sheet", "parse_error")
    op.drop_column("sheet", "parsed_at")
