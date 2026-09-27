"""Sheet views: each view's kind, extent, scale verdict, grid and calibration (FR-VIS-05/07/08).

A sheet holds one or more views (a plan, an enlarged plan, sections), each with its own scale.
`scale_status` is what measurement checks: a length is produced only from a view whose scale
is verified by its own dimensions, or calibrated by a named person (`calibrated_by`).

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCALE_STATUSES = "('verified', 'unverified', 'conflicting', 'nts', 'calibrated')"


def upgrade() -> None:
    op.create_table(
        "sheet_view",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("sheet_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("title", sa.String(300), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("extent", postgresql.JSONB(), nullable=False),
        sa.Column("level", sa.String(40), nullable=True),
        sa.Column("stated_scale", sa.String(80), nullable=True),
        sa.Column("stated_denominator", sa.Float(), nullable=True),
        sa.Column("scale_status", sa.String(16), nullable=False),
        sa.Column("denominator", sa.Float(), nullable=True),
        sa.Column("scale_evidence", postgresql.JSONB(), nullable=False),
        sa.Column("grid", postgresql.JSONB(), nullable=True),
        sa.Column("grid_box", postgresql.JSONB(), nullable=True),
        sa.Column("detector_version", sa.String(16), nullable=False),
        sa.Column("calibration", postgresql.JSONB(), nullable=True),
        sa.Column("calibrated_by", sa.String(200), nullable=True),
        sa.Column("calibrated_by_id", sa.Uuid(), nullable=True),
        sa.Column("calibrated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_sheet_view_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["sheet_id"],
            ["sheet.id"],
            name=op.f("fk_sheet_view_sheet_id_sheet"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sheet_view")),
        sa.UniqueConstraint("sheet_id", "ordinal", name=op.f("uq_sheet_view_sheet_id")),
        sa.CheckConstraint(
            f"scale_status IN {SCALE_STATUSES}", name=op.f("ck_sheet_view_scale_status_known")
        ),
        # Measurable means a scale to measure with, and a calibration always names its person.
        sa.CheckConstraint(
            "scale_status NOT IN ('verified', 'calibrated') OR denominator IS NOT NULL",
            name=op.f("ck_sheet_view_measurable_has_scale"),
        ),
        sa.CheckConstraint(
            "scale_status <> 'calibrated' OR calibrated_by IS NOT NULL",
            name=op.f("ck_sheet_view_calibration_named"),
        ),
    )
    op.create_index(op.f("ix_sheet_view_bid_id"), "sheet_view", ["bid_id"])
    op.create_index(op.f("ix_sheet_view_sheet_id"), "sheet_view", ["sheet_id"])

    op.execute("ALTER TABLE sheet_view ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY firebid_app_bid_scope ON sheet_view FOR ALL TO firebid_app "
        "USING (firebid_can_see_bid(bid_id)) WITH CHECK (firebid_can_see_bid(bid_id))"
    )
    op.execute(
        "CREATE POLICY firebid_service_all ON sheet_view FOR ALL TO firebid_service "
        "USING (true) WITH CHECK (true)"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_sheet_view_sheet_id"), table_name="sheet_view")
    op.drop_index(op.f("ix_sheet_view_bid_id"), table_name="sheet_view")
    op.drop_table("sheet_view")
