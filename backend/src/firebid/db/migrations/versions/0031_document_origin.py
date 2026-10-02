"""Folder intake: a document's path in the folder it came from, and whose document it is.

`document.source_path`: the file's path within the uploaded folder or archive (FR-DOC-09).
`document.origin`: tender, working or reference (FR-DOC-10). Only a tender document whose
origin is confirmed is read. Every document uploaded before this migration was uploaded as a
tender document, and is marked so.

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("document", sa.Column("source_path", sa.String(1024), nullable=True))
    op.add_column(
        "document", sa.Column("origin", sa.String(16), nullable=False, server_default="tender")
    )
    op.add_column(
        "document",
        sa.Column("origin_status", sa.String(16), nullable=False, server_default="confirmed"),
    )
    op.add_column("document", sa.Column("origin_reason", sa.Text(), nullable=True))
    op.add_column("document", sa.Column("origin_by", sa.String(200), nullable=True))
    op.add_column("document", sa.Column("origin_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        op.f("ck_document_origin_known"),
        "document",
        "origin IN ('tender', 'working', 'reference')",
    )
    op.create_check_constraint(
        op.f("ck_document_origin_status_known"),
        "document",
        "origin_status IN ('proposed', 'confirmed')",
    )
    op.create_index(op.f("ix_document_origin"), "document", ["origin"])


def downgrade() -> None:
    op.drop_index(op.f("ix_document_origin"), table_name="document")
    op.drop_constraint(op.f("ck_document_origin_status_known"), "document", type_="check")
    op.drop_constraint(op.f("ck_document_origin_known"), "document", type_="check")
    for column in (
        "origin_at",
        "origin_by",
        "origin_reason",
        "origin_status",
        "origin",
        "source_path",
    ):
        op.drop_column("document", column)
