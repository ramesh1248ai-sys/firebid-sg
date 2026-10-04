"""Tender clarifications and qualifications (P2-06).

`clarification`, `clarification_source` and `qualification`, all bid-scoped and under
row-level security. A clarification cannot be stored without an evidence reference.

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-04
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMPTY = sa.text("'{}'::jsonb")
EMPTY_LIST = sa.text("'[]'::jsonb")
TABLES = ("clarification", "clarification_source", "qualification")


def _common(table: str) -> list[Any]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f(f"fk_{table}_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
    ]


def upgrade() -> None:
    op.create_table(
        "clarification",
        *_common("clarification"),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(300), nullable=False),
        sa.Column("project", sa.String(300), nullable=False),
        sa.Column("level_grid", sa.String(200), nullable=False, server_default=""),
        sa.Column("sheets", postgresql.JSONB(), nullable=False, server_default=EMPTY_LIST),
        sa.Column("problem", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("options", postgresql.JSONB(), nullable=False, server_default=EMPTY_LIST),
        sa.Column("cost_impact", sa.Text(), nullable=False, server_default=""),
        sa.Column("programme_impact", sa.Text(), nullable=False, server_default=""),
        sa.Column("required_reviewer", sa.String(24), nullable=False),
        sa.Column("engineering_content", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("engineering_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("qp_input_needed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("drafting", postgresql.JSONB(), nullable=False, server_default=EMPTY),
        sa.Column("design_approved_by", sa.String(200), nullable=True),
        sa.Column("design_approved_by_id", sa.Uuid(), nullable=True),
        sa.Column("design_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("design_note", sa.Text(), nullable=True),
        sa.Column("approved_by", sa.String(200), nullable=True),
        sa.Column("approved_by_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_by", sa.String(200), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_summary", sa.Text(), nullable=True),
        sa.Column("response_document_id", sa.Uuid(), nullable=True),
        sa.Column("impact_task_id", sa.Uuid(), nullable=True),
        sa.Column("impact_outcome", sa.String(16), nullable=True),
        sa.Column("impact_note", sa.Text(), nullable=True),
        sa.Column("impact_detail", postgresql.JSONB(), nullable=False, server_default=EMPTY),
        sa.Column("impact_by", sa.String(200), nullable=True),
        sa.Column("impact_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f("fk_clarification_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["response_document_id"],
            ["document.id"],
            name=op.f("fk_clarification_response_document_id_document"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["impact_task_id"],
            ["human_task.id"],
            name=op.f("fk_clarification_impact_task_id_human_task"),
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("bid_id", "number", name=op.f("uq_clarification_number")),
        sa.CheckConstraint(
            "kind IN ('tender_clarification', 'construction_rfi')",
            name=op.f("ck_clarification_kind_known"),
        ),
        sa.CheckConstraint(
            "state IN ('draft', 'internal_review', 'approved_to_issue', 'issued', 'responded', "
            "'closed_incorporated', 'closed_no_change', 'converted_to_qualification')",
            name=op.f("ck_clarification_state_known"),
        ),
        sa.CheckConstraint(
            "jsonb_array_length(evidence) >= 1", name=op.f("ck_clarification_has_evidence")
        ),
        sa.CheckConstraint(
            "required_reviewer IN ('bid_manager', 'design_manager')",
            name=op.f("ck_clarification_reviewer_known"),
        ),
        sa.CheckConstraint(
            "impact_outcome IS NULL OR impact_outcome IN ('incorporated', 'no_change')",
            name=op.f("ck_clarification_outcome_known"),
        ),
    )
    op.create_index(op.f("ix_clarification_bid_id"), "clarification", ["bid_id"])
    op.create_index(op.f("ix_clarification_state"), "clarification", ["state"])

    op.create_table(
        "clarification_source",
        *_common("clarification_source"),
        sa.Column("clarification_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("ref", sa.String(200), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False, server_default=EMPTY),
        sa.ForeignKeyConstraint(
            ["clarification_id"],
            ["clarification.id"],
            name=op.f("fk_clarification_source_clarification_id_clarification"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("bid_id", "kind", "ref", name=op.f("uq_clarification_source")),
        sa.CheckConstraint(
            "kind IN ('spec_issue', 'boq_variance', 'scope_row', 'missing_information')",
            name=op.f("ck_clarification_source_kind_known"),
        ),
    )
    op.create_index(op.f("ix_clarification_source_bid_id"), "clarification_source", ["bid_id"])
    op.create_index(
        op.f("ix_clarification_source_clarification_id"),
        "clarification_source",
        ["clarification_id"],
    )

    op.create_table(
        "qualification",
        *_common("qualification"),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("clarification_id", sa.Uuid(), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("decided_by", sa.String(200), nullable=True),
        sa.Column("decided_by_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["clarification_id"],
            ["clarification.id"],
            name=op.f("fk_qualification_clarification_id_clarification"),
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("clarification_id", name=op.f("uq_qualification_clarification")),
        sa.CheckConstraint(
            "kind IN ('qualification', 'assumption')", name=op.f("ck_qualification_kind_known")
        ),
        sa.CheckConstraint(
            "state IN ('proposed', 'accepted', 'rejected')",
            name=op.f("ck_qualification_state_known"),
        ),
        sa.CheckConstraint(
            "state = 'proposed' OR decided_by IS NOT NULL",
            name=op.f("ck_qualification_decision_named"),
        ),
    )
    op.create_index(op.f("ix_qualification_bid_id"), "qualification", ["bid_id"])
    op.create_index(
        op.f("ix_qualification_clarification_id"), "qualification", ["clarification_id"]
    )

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY firebid_app_bid_scope ON {table} FOR ALL TO firebid_app "
            "USING (firebid_can_see_bid(bid_id)) WITH CHECK (firebid_can_see_bid(bid_id))"
        )
        op.execute(
            f"CREATE POLICY firebid_service_all ON {table} FOR ALL TO firebid_service "
            "USING (true) WITH CHECK (true)"
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
