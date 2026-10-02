"""Each sheet's own detection outcome: detection is a job a sheet (ADR-010, amended).

`sheet.detected_at` is when the sheet's `detection.sheet` job finished; `sheet.detection_error`
is why it failed, if it did. Sheets of documents already read were detected in their
document's finish job: they are marked detected now.

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sheet", sa.Column("detected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sheet", sa.Column("detection_error", sa.Text(), nullable=True))
    op.execute(
        "UPDATE sheet SET detected_at = sheet.parsed_at FROM document "
        "WHERE document.id = sheet.document_id AND document.state = 'done'"
    )


def downgrade() -> None:
    op.drop_column("sheet", "detection_error")
    op.drop_column("sheet", "detected_at")
