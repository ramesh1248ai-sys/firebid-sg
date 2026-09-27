"""Title block readings, revision sources, and remembered layouts (FR-DOC-02, FR-DOC-04).

A sheet revision is now created as a proposal from its title block, so it records how each
field was read (`reading`) and what every source said its revision was (`sources`): title
block, filename and transmittal. When they disagree the revision waits in Conflict, and
`conflict_reason` says why in words a person can act on.

The same drawing often arrives twice, as a PDF and as a DWG. That is one revision with two
renditions, not two revisions, so the other sheets are listed in `alternate_sheet_ids`.

"Exactly one Current revision per sheet number" was enforced only by a guard in application
code. A partial unique index makes the database refuse a second one as well, so a race
between two workers cannot leave two Current revisions feeding takeoff (guardrail 6).

A number or revision the title block did not yield stays null rather than being invented,
which is only allowed while the revision is still `received`.

`title_block_layout` remembers where one consultant puts each field, learnt when a person
confirms a reading, so later sheets are read by position. It is organisation-level: it holds
positions and a consultant's name, not tender content.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sheet_revision", sa.Column("reading", postgresql.JSONB(), nullable=True))
    op.add_column("sheet_revision", sa.Column("sources", postgresql.JSONB(), nullable=True))
    op.add_column("sheet_revision", sa.Column("conflict_reason", sa.Text(), nullable=True))
    op.add_column(
        "sheet_revision",
        sa.Column(
            "alternate_sheet_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column("sheet_revision", "sheet_number", nullable=True)
    op.alter_column("sheet_revision", "revision_label", nullable=True)
    op.create_check_constraint(
        op.f("ck_sheet_revision_identified_unless_received"),
        "sheet_revision",
        "(sheet_number IS NOT NULL AND revision_label IS NOT NULL) "
        "OR state IN ('received', 'withdrawn')",
    )
    op.create_index(
        "uq_sheet_revision_one_current",
        "sheet_revision",
        ["bid_id", "sheet_number"],
        unique=True,
        postgresql_where=sa.text("state = 'current'"),
    )

    op.create_table(
        "title_block_layout",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("consultant", sa.String(200), nullable=False),
        sa.Column("fingerprint", postgresql.JSONB(), nullable=False),
        sa.Column("layout", postgresql.JSONB(), nullable=False),
        sa.Column("times_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_title_block_layout_organisation_id_organisation"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f("fk_title_block_layout_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_title_block_layout")),
    )
    op.create_index(
        op.f("ix_title_block_layout_organisation_id"), "title_block_layout", ["organisation_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_title_block_layout_organisation_id"), table_name="title_block_layout")
    op.drop_table("title_block_layout")
    op.drop_index("uq_sheet_revision_one_current", table_name="sheet_revision")
    op.drop_constraint(
        op.f("ck_sheet_revision_identified_unless_received"), "sheet_revision", type_="check"
    )
    # Rows proposed without a number or revision cannot survive the old NOT NULL.
    op.execute("DELETE FROM sheet_revision WHERE sheet_number IS NULL OR revision_label IS NULL")
    op.alter_column("sheet_revision", "revision_label", nullable=False)
    op.alter_column("sheet_revision", "sheet_number", nullable=False)
    op.drop_column("sheet_revision", "alternate_sheet_ids")
    op.drop_column("sheet_revision", "conflict_reason")
    op.drop_column("sheet_revision", "sources")
    op.drop_column("sheet_revision", "reading")
