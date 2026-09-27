"""What kind of tender document each file is (FR-DOC-02).

`doc_type` is a proposal until a person confirms it, like every classification: the rules or
the model propose, and `classification` records which did, with what confidence and why. It
keeps the digest the answer was based on, so a person reviewing it sees the same evidence.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOC_TYPES = (
    "drawing",
    "specification",
    "boq",
    "schedule",
    "addendum",
    "clarification_response",
    "contract_conditions",
    "other",
)


def upgrade() -> None:
    op.add_column("document", sa.Column("doc_type", sa.String(32), nullable=True))
    op.add_column("document", sa.Column("doc_type_confidence", sa.Float(), nullable=True))
    op.add_column("document", sa.Column("classification", postgresql.JSONB(), nullable=True))
    op.create_check_constraint(
        op.f("ck_document_doc_type_known"),
        "document",
        f"doc_type IS NULL OR doc_type IN {DOC_TYPES}",
    )
    op.create_index(op.f("ix_document_doc_type"), "document", ["doc_type"])


def downgrade() -> None:
    op.drop_index(op.f("ix_document_doc_type"), table_name="document")
    op.drop_constraint(op.f("ck_document_doc_type_known"), "document", type_="check")
    op.drop_column("document", "classification")
    op.drop_column("document", "doc_type_confidence")
    op.drop_column("document", "doc_type")
