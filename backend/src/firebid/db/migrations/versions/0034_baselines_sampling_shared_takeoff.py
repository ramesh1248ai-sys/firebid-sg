"""Baselines, sampling and shared takeoffs (P2-02).

* `takeoff_snapshot`, `review_sample`: bid-scoped, under row-level security.
* `coverage_policy`: organisation-level, versioned, like `measurement_rule`.
* `shared_takeoff`: project-level. A member of any bid of the project may read it; only a
  member of the bid it is published from may write it (ADR-012).
* `approval.reopened_at`, `reopened_reason`, `reopened_items`: a gate approval that a later
  change to the takeoff reopened, and the items that reopened it (FR-QTO-12).

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-03
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMPTY = sa.text("'{}'::jsonb")


def _created() -> sa.Column[datetime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


PROJECT_HELPER = """
-- A project is visible to whoever is on the team of any of its bids. SECURITY DEFINER, as
-- firebid_can_see_bid is: it reads membership as the owner.
CREATE OR REPLACE FUNCTION firebid_can_see_project(target uuid) RETURNS boolean AS $$
    SELECT target IS NOT NULL AND EXISTS (
        SELECT 1 FROM bid b JOIN bid_member m ON m.bid_id = b.id
        WHERE b.project_id = target AND m.user_id = firebid_current_user_id()
    )
$$ LANGUAGE sql STABLE SECURITY DEFINER;
"""


def _bid_scoped(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY firebid_app_bid_scope ON {table} FOR ALL TO firebid_app "
        "USING (firebid_can_see_bid(bid_id)) WITH CHECK (firebid_can_see_bid(bid_id))"
    )
    op.execute(
        f"CREATE POLICY firebid_service_all ON {table} FOR ALL TO firebid_service "
        "USING (true) WITH CHECK (true)"
    )


def upgrade() -> None:
    op.add_column("approval", sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("approval", sa.Column("reopened_reason", sa.Text(), nullable=True))
    op.add_column("approval", sa.Column("reopened_items", postgresql.JSONB(), nullable=True))

    op.create_table(
        "takeoff_snapshot",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("addendum_id", sa.Uuid(), nullable=True),
        sa.Column("approval_id", sa.Uuid(), nullable=True),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("items", postgresql.JSONB(), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_takeoff_snapshot_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["addendum_id"],
            ["addendum.id"],
            name=op.f("fk_takeoff_snapshot_addendum_id_addendum"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["approval.id"],
            name=op.f("fk_takeoff_snapshot_approval_id_approval"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_takeoff_snapshot")),
    )
    op.create_index(op.f("ix_takeoff_snapshot_bid_id"), "takeoff_snapshot", ["bid_id"])
    op.create_index(op.f("ix_takeoff_snapshot_addendum_id"), "takeoff_snapshot", ["addendum_id"])
    _bid_scoped("takeoff_snapshot")

    op.create_table(
        "coverage_policy",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("tolerable_error_percent", sa.Numeric(6, 3), nullable=False),
        sa.Column("confidence_percent", sa.Numeric(6, 3), nullable=False),
        sa.Column("accept_errors", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        _created(),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_coverage_policy_organisation_id_organisation"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f("fk_coverage_policy_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_coverage_policy")),
        sa.UniqueConstraint(
            "organisation_id", "category", "version", name=op.f("uq_coverage_policy")
        ),
        sa.CheckConstraint(
            "mode IN ('full', 'sampling')", name=op.f("ck_coverage_policy_mode_known")
        ),
    )
    op.create_index(
        op.f("ix_coverage_policy_organisation_id"), "coverage_policy", ["organisation_id"]
    )

    op.create_table(
        "review_sample",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("plan", postgresql.JSONB(), nullable=False),
        sa.Column("lot", postgresql.JSONB(), nullable=False),
        sa.Column("sample", postgresql.JSONB(), nullable=False),
        sa.Column("seed", sa.String(40), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("outcomes", postgresql.JSONB(), nullable=False, server_default=EMPTY),
        sa.Column("drawn_by_id", sa.Uuid(), nullable=True),
        sa.Column("drawn_by", sa.String(200), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(200), nullable=True),
        _created(),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_review_sample_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["drawn_by_id"],
            ["app_user.id"],
            name=op.f("fk_review_sample_drawn_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_sample")),
        sa.CheckConstraint(
            "status IN ('drawn', 'passed', 'accepted', 'escalated')",
            name=op.f("ck_review_sample_status_known"),
        ),
    )
    op.create_index(op.f("ix_review_sample_bid_id"), "review_sample", ["bid_id"])
    op.create_index(op.f("ix_review_sample_category"), "review_sample", ["category"])
    _bid_scoped("review_sample")

    op.execute(PROJECT_HELPER)
    op.create_table(
        "shared_takeoff",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("source_bid_id", sa.Uuid(), nullable=False),
        sa.Column("source_bid_human_id", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("items", postgresql.JSONB(), nullable=False),
        sa.Column("published_by_id", sa.Uuid(), nullable=True),
        sa.Column("published_by", sa.String(200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        _created(),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_shared_takeoff_organisation_id_organisation"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_shared_takeoff_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_bid_id"],
            ["bid.id"],
            name=op.f("fk_shared_takeoff_source_bid_id_bid"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["published_by_id"],
            ["app_user.id"],
            name=op.f("fk_shared_takeoff_published_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shared_takeoff")),
        sa.UniqueConstraint("project_id", "version", name=op.f("uq_shared_takeoff_version")),
    )
    op.create_index(op.f("ix_shared_takeoff_project_id"), "shared_takeoff", ["project_id"])
    op.execute("ALTER TABLE shared_takeoff ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY firebid_app_project_read ON shared_takeoff FOR SELECT TO firebid_app "
        "USING (firebid_can_see_project(project_id))"
    )
    op.execute(
        "CREATE POLICY firebid_app_source_write ON shared_takeoff FOR INSERT TO firebid_app "
        "WITH CHECK (firebid_can_see_bid(source_bid_id))"
    )
    op.execute(
        "CREATE POLICY firebid_service_all ON shared_takeoff FOR ALL TO firebid_service "
        "USING (true) WITH CHECK (true)"
    )


def downgrade() -> None:
    op.drop_table("shared_takeoff")
    op.execute("DROP FUNCTION IF EXISTS firebid_can_see_project(uuid)")
    op.drop_table("review_sample")
    op.drop_table("coverage_policy")
    op.drop_table("takeoff_snapshot")
    op.drop_column("approval", "reopened_items")
    op.drop_column("approval", "reopened_reason")
    op.drop_column("approval", "reopened_at")
