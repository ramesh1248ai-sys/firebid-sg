"""The frozen submission, tender outcomes and library proposals (P2-08).

* `submission_snapshot`: one a bid, append-only (the database refuses UPDATE and DELETE),
  under row-level security.
* `bid_outcome`: one a bid, under row-level security.
* `library_proposal`: organisation-level.

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-04
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMPTY = sa.text("'{}'::jsonb")
EMPTY_LIST = sa.text("'[]'::jsonb")
BID_SCOPED = ("submission_snapshot", "bid_outcome")


def _created() -> sa.Column[Any]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "submission_snapshot",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("manifest_key", sa.String(512), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("files", postgresql.JSONB(), nullable=False, server_default=EMPTY_LIST),
        sa.Column("record_counts", postgresql.JSONB(), nullable=False, server_default=EMPTY),
        sa.Column("frozen_by", sa.String(200), nullable=False),
        sa.Column("frozen_by_id", sa.Uuid(), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["bid_id"],
            ["bid.id"],
            name=op.f("fk_submission_snapshot_bid_id_bid"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submission_snapshot")),
        sa.UniqueConstraint("bid_id", name=op.f("uq_submission_snapshot_bid")),
    )
    op.create_index(op.f("ix_submission_snapshot_bid_id"), "submission_snapshot", ["bid_id"])
    # FR-PKG-03: a frozen submission is never changed. `firebid_reject_write` is the function
    # the audit tables use (migration 0002).
    op.execute(
        "CREATE TRIGGER submission_snapshot_append_only BEFORE UPDATE OR DELETE ON "
        "submission_snapshot FOR EACH ROW EXECUTE FUNCTION firebid_reject_write()"
    )

    op.create_table(
        "bid_outcome",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("awarded_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("reasons", sa.Text(), nullable=False, server_default=""),
        sa.Column("competitor_feedback", sa.Text(), nullable=False, server_default=""),
        sa.Column("recorded_by", sa.String(200), nullable=False),
        sa.Column("recorded_by_id", sa.Uuid(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_bid_outcome_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bid_outcome")),
        sa.UniqueConstraint("bid_id", name=op.f("uq_bid_outcome_bid")),
        sa.CheckConstraint(
            "outcome IN ('awarded', 'lost', 'withdrawn')", name=op.f("ck_bid_outcome_outcome_known")
        ),
    )
    op.create_index(op.f("ix_bid_outcome_bid_id"), "bid_outcome", ["bid_id"])
    op.create_index(op.f("ix_bid_outcome_outcome"), "bid_outcome", ["outcome"])

    for table in BID_SCOPED:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY firebid_app_bid_scope ON {table} FOR ALL TO firebid_app "
            "USING (firebid_can_see_bid(bid_id)) WITH CHECK (firebid_can_see_bid(bid_id))"
        )
        op.execute(
            f"CREATE POLICY firebid_service_all ON {table} FOR ALL TO firebid_service "
            "USING (true) WITH CHECK (true)"
        )

    op.create_table(
        "library_proposal",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("library", sa.String(16), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("source_ref", sa.String(200), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("proposed_by", sa.String(200), nullable=False),
        sa.Column("proposed_by_id", sa.Uuid(), nullable=True),
        sa.Column("decided_by", sa.String(200), nullable=True),
        sa.Column("decided_by_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("applied_entry_id", sa.Uuid(), nullable=True),
        _created(),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_library_proposal_organisation_id_organisation"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_library_proposal")),
        sa.CheckConstraint(
            "library IN ('rate', 'productivity')", name=op.f("ck_library_proposal_library_known")
        ),
        sa.CheckConstraint(
            "source IN ('quotation', 'outcome', 'estimator')",
            name=op.f("ck_library_proposal_source_known"),
        ),
        sa.CheckConstraint(
            "state IN ('proposed', 'approved', 'rejected')",
            name=op.f("ck_library_proposal_state_known"),
        ),
        sa.CheckConstraint(
            "state = 'proposed' OR decided_by IS NOT NULL",
            name=op.f("ck_library_proposal_decision_named"),
        ),
        sa.CheckConstraint(
            "applied_entry_id IS NULL OR state = 'approved'",
            name=op.f("ck_library_proposal_applied_approved"),
        ),
    )
    op.create_index(
        op.f("ix_library_proposal_organisation_id"), "library_proposal", ["organisation_id"]
    )
    op.create_index(op.f("ix_library_proposal_state"), "library_proposal", ["state"])


def downgrade() -> None:
    op.drop_table("library_proposal")
    op.drop_table("bid_outcome")
    op.execute("DROP TRIGGER IF EXISTS submission_snapshot_append_only ON submission_snapshot")
    op.drop_table("submission_snapshot")
