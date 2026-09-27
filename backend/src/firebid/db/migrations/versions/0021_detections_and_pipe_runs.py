"""Detections and pipe runs (P1-05): FR-VIS-03, 06, 07, 09.

* `detected_object` gains what a detection must carry: its kind (object, riser, drop), grid
  reference, level, orientation, raw and calibrated confidence with the calibration version,
  the features the confidence came from, and `gaps`, which says why a sheet could not give a
  view or grid reference. A check constraint makes "located, or saying why" a rule of the
  database, not only of the code that writes it.
* `pipe_run`: each run with its class, size (and how it was found), paper length, and a
  length only where the view is measurable. Row-level security per bid.
* `consultant_profile`: how a consultant draws pipe (layers and colours), versioned beside
  the consultant's symbol mappings; organisation-level, like them.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCATED = (
    "(view_id IS NOT NULL OR gaps ? 'view') "
    "AND (grid_reference IS NOT NULL OR gaps ? 'grid_reference')"
)
EMPTY = sa.text("'{}'::jsonb")


def upgrade() -> None:
    for column in (
        sa.Column("kind", sa.String(16), nullable=False, server_default="object"),
        sa.Column("grid_reference", sa.String(40), nullable=True),
        sa.Column("level", sa.String(40), nullable=True),
        sa.Column("orientation", sa.Float(), nullable=True),
        sa.Column("raw_confidence", sa.Float(), nullable=True),
        sa.Column("features", postgresql.JSONB(), nullable=False, server_default=EMPTY),
        sa.Column("calibration_version", sa.String(40), nullable=True),
        sa.Column("detector_version", sa.String(16), nullable=True),
        sa.Column("gaps", postgresql.JSONB(), nullable=False, server_default=EMPTY),
        sa.Column("state", sa.String(16), nullable=False, server_default="proposed"),
    ):
        op.add_column("detected_object", column)
    op.create_check_constraint(
        op.f("ck_detected_object_kind_known"),
        "detected_object",
        "kind IN ('object', 'riser', 'drop')",
    )
    op.create_check_constraint(
        op.f("ck_detected_object_located_or_says_why"), "detected_object", LOCATED
    )

    op.create_table(
        "pipe_run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("sheet_id", sa.Uuid(), nullable=False),
        sa.Column("sheet_revision_id", sa.Uuid(), nullable=True),
        sa.Column("view_id", sa.Uuid(), nullable=True),
        sa.Column("run_index", sa.Integer(), nullable=False),
        sa.Column("run_class", sa.String(16), nullable=False),
        sa.Column("nominal_dn", sa.Integer(), nullable=True),
        sa.Column("size_status", sa.String(16), nullable=False),
        sa.Column("size_reason", sa.Text(), nullable=True),
        sa.Column("labels", postgresql.JSONB(), nullable=False),
        sa.Column("paper_length_mm", sa.Float(), nullable=False),
        sa.Column("length_mm", sa.Float(), nullable=True),
        sa.Column("points", postgresql.JSONB(), nullable=False),
        sa.Column("geometry_rows", postgresql.JSONB(), nullable=False),
        sa.Column("grid_reference", sa.String(40), nullable=True),
        sa.Column("level", sa.String(40), nullable=True),
        sa.Column("features", postgresql.JSONB(), nullable=False),
        sa.Column("raw_confidence", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("calibration_version", sa.String(40), nullable=False),
        sa.Column("detector_version", sa.String(16), nullable=False),
        sa.Column("gaps", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_pipe_run_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["sheet_id"], ["sheet.id"], name=op.f("fk_pipe_run_sheet_id_sheet"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pipe_run")),
        sa.CheckConstraint("run_class IN ('main', 'branch')", name=op.f("ck_pipe_run_class_known")),
        sa.CheckConstraint(
            "size_status IN ('labelled', 'propagated', 'conflict', 'unknown')",
            name=op.f("ck_pipe_run_size_status_known"),
        ),
        sa.CheckConstraint(
            "size_status <> 'conflict' OR nominal_dn IS NULL",
            name=op.f("ck_pipe_run_conflict_unsized"),
        ),
        sa.CheckConstraint(LOCATED, name=op.f("ck_pipe_run_located_or_says_why")),
    )
    for name in ("bid_id", "sheet_id", "sheet_revision_id"):
        op.create_index(op.f(f"ix_pipe_run_{name}"), "pipe_run", [name])
    op.execute("ALTER TABLE pipe_run ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY firebid_app_bid_scope ON pipe_run FOR ALL TO firebid_app "
        "USING (firebid_can_see_bid(bid_id)) WITH CHECK (firebid_can_see_bid(bid_id))"
    )
    op.execute(
        "CREATE POLICY firebid_service_all ON pipe_run FOR ALL TO firebid_service "
        "USING (true) WITH CHECK (true)"
    )

    op.create_table(
        "consultant_profile",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("consultant_key", sa.String(200), nullable=False),
        sa.Column("consultant", sa.String(200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("pipe_layers", postgresql.JSONB(), nullable=False),
        sa.Column("pipe_colours", postgresql.JSONB(), nullable=False),
        sa.Column("changed_by", sa.String(200), nullable=True),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_consultant_profile_organisation_id_organisation"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["consultant_profile.id"],
            name=op.f("fk_consultant_profile_supersedes_id_consultant_profile"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f("fk_consultant_profile_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consultant_profile")),
        sa.UniqueConstraint(
            "organisation_id",
            "consultant_key",
            "version",
            name=op.f("uq_consultant_profile_organisation_id"),
        ),
    )
    op.create_index(
        op.f("ix_consultant_profile_organisation_id"), "consultant_profile", ["organisation_id"]
    )


def downgrade() -> None:
    op.drop_table("consultant_profile")
    op.drop_table("pipe_run")
    op.drop_constraint(
        op.f("ck_detected_object_located_or_says_why"), "detected_object", type_="check"
    )
    op.drop_constraint(op.f("ck_detected_object_kind_known"), "detected_object", type_="check")
    for name in (
        "state",
        "gaps",
        "detector_version",
        "calibration_version",
        "features",
        "raw_confidence",
        "orientation",
        "level",
        "grid_reference",
        "kind",
    ):
        op.drop_column("detected_object", name)
