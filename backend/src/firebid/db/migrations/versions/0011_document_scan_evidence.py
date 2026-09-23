"""When each uploaded file was scanned, and what was found (NFR-06).

`state` already says where a document ended up, but it is overwritten as the document moves
on. These two columns are the evidence that the scan happened at all, which is what an auditor
asks for after an incident: not "is this file clean now" but "was it checked before it was
opened, and when".

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("document", sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("document", sa.Column("scan_signature", sa.String(200), nullable=True))
    # Files held by a scanner outage are retried by a sweep, which looks for exactly this.
    op.create_index(
        "ix_document_awaiting_scan",
        "document",
        ["bid_id"],
        postgresql_where=sa.text("state = 'awaiting_scan'"),
    )


def downgrade() -> None:
    op.drop_index("ix_document_awaiting_scan", table_name="document")
    op.drop_column("document", "scan_signature")
    op.drop_column("document", "scanned_at")
