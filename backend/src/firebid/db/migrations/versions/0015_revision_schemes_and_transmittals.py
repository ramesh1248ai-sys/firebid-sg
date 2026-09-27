"""Revision schemes per project, and transmittal entries (FR-DOC-04).

`project.revision_scheme` names the scheme in `config/revisions.yaml` that orders this
project's revisions; null means the default. A name rather than the scheme itself, so the
schemes stay configuration with effective dates and a project follows its scheme's history.

`transmittal_entry` holds what a drawing list or transmittal says was issued: drawing number,
revision, date. It is the third source a revision is checked against, beside the title block
and the filename, and each entry points at the workbook it was read from.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("project", sa.Column("revision_scheme", sa.String(64), nullable=True))

    op.create_table(
        "transmittal_entry",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("tender_package_id", sa.Uuid(), nullable=True),
        sa.Column("sheet_number", sa.String(120), nullable=False),
        sa.Column("revision_label", sa.String(40), nullable=False),
        sa.Column("issued_on", sa.Date(), nullable=True),
        sa.Column("title", sa.String(300), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_transmittal_entry_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["document.id"],
            name=op.f("fk_transmittal_entry_document_id_document"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tender_package_id"],
            ["tender_package.id"],
            name=op.f("fk_transmittal_entry_tender_package_id_tender_package"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transmittal_entry")),
        sa.UniqueConstraint(
            "document_id",
            "sheet_number",
            "revision_label",
            name=op.f("uq_transmittal_entry_document_id"),
        ),
    )
    op.create_index(op.f("ix_transmittal_entry_bid_id"), "transmittal_entry", ["bid_id"])
    op.create_index(
        op.f("ix_transmittal_entry_sheet_number"), "transmittal_entry", ["sheet_number"]
    )

    # Bid-scoped, so membership is enforced by the database as well (guardrail 8).
    op.execute("ALTER TABLE transmittal_entry ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY firebid_app_bid_scope ON transmittal_entry FOR ALL TO firebid_app "
        "USING (firebid_can_see_bid(bid_id)) WITH CHECK (firebid_can_see_bid(bid_id))"
    )
    op.execute(
        "CREATE POLICY firebid_service_all ON transmittal_entry FOR ALL TO firebid_service "
        "USING (true) WITH CHECK (true)"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_transmittal_entry_sheet_number"), table_name="transmittal_entry")
    op.drop_index(op.f("ix_transmittal_entry_bid_id"), table_name="transmittal_entry")
    op.drop_table("transmittal_entry")
    op.drop_column("project", "revision_scheme")
