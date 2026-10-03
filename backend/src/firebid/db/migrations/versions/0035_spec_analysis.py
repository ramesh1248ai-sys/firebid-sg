"""Full specification analysis (P2-03): obligations, issues and the scope matrix.

`spec_obligation`, `spec_issue` and `scope_row`, all bid-scoped under row-level security.

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-03
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMPTY = sa.text("'{}'::jsonb")
TABLES = ("spec_obligation", "spec_issue", "scope_row")


def _created() -> sa.Column[datetime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _decision() -> list[sa.Column[Any]]:
    return [
        sa.Column("decided_by", sa.String(200), nullable=True),
        sa.Column("decided_by_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
    ]


def _bid(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["bid_id"], ["bid.id"], name=op.f(f"fk_{table}_bid_id_bid"), ondelete="CASCADE"
    )


def upgrade() -> None:
    op.create_table(
        "spec_obligation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("document_revision_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("clause_id", sa.Uuid(), nullable=True),
        sa.Column("clause_number", sa.String(40), nullable=False),
        sa.Column("system", sa.String(24), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("quantities", postgresql.JSONB(), nullable=False, server_default=EMPTY),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("citation_ok", sa.Boolean(), nullable=False),
        sa.Column("citation_reason", sa.Text(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("provenance", postgresql.JSONB(), nullable=False, server_default=EMPTY),
        *_decision(),
        _created(),
        _bid("spec_obligation"),
        sa.ForeignKeyConstraint(
            ["document_revision_id"],
            ["document_revision.id"],
            name=op.f("fk_spec_obligation_document_revision_id_document_revision"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["clause_id"],
            ["spec_clause.id"],
            name=op.f("fk_spec_obligation_clause_id_spec_clause"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spec_obligation")),
        sa.UniqueConstraint("document_revision_id", "key", name=op.f("uq_spec_obligation_key")),
        sa.CheckConstraint(
            "state IN ('proposed', 'verified', 'rejected')",
            name=op.f("ck_spec_obligation_state_known"),
        ),
        sa.CheckConstraint(
            "method IN ('rule', 'model', 'person')", name=op.f("ck_spec_obligation_method_known")
        ),
        sa.CheckConstraint(
            "state = 'proposed' OR decided_by IS NOT NULL",
            name=op.f("ck_spec_obligation_decision_named"),
        ),
    )
    op.create_index(op.f("ix_spec_obligation_bid_id"), "spec_obligation", ["bid_id"])
    op.create_index(
        op.f("ix_spec_obligation_document_revision_id"), "spec_obligation", ["document_revision_id"]
    )
    op.create_index(op.f("ix_spec_obligation_system"), "spec_obligation", ["system"])
    op.create_index(op.f("ix_spec_obligation_category"), "spec_obligation", ["category"])

    op.create_table(
        "spec_issue",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("rule", sa.String(60), nullable=False),
        sa.Column("severity", sa.String(8), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("system", sa.String(24), nullable=True),
        sa.Column("spec_ref", postgresql.JSONB(), nullable=False),
        sa.Column("drawing_ref", postgresql.JSONB(), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default=EMPTY),
        sa.Column("rules_version", sa.String(40), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        *_decision(),
        sa.Column("last_found_at", sa.DateTime(timezone=True), nullable=True),
        _created(),
        _bid("spec_issue"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spec_issue")),
        sa.UniqueConstraint("bid_id", "key", name=op.f("uq_spec_issue_key")),
        sa.CheckConstraint(
            "state IN ('open', 'dismissed', 'resolved')", name=op.f("ck_spec_issue_state_known")
        ),
        sa.CheckConstraint(
            "category IN ('conflict', 'missing', 'ambiguous')",
            name=op.f("ck_spec_issue_category_known"),
        ),
        sa.CheckConstraint(
            "state <> 'dismissed' OR decided_by IS NOT NULL",
            name=op.f("ck_spec_issue_dismissal_named"),
        ),
    )
    op.create_index(op.f("ix_spec_issue_bid_id"), "spec_issue", ["bid_id"])
    op.create_index(op.f("ix_spec_issue_category"), "spec_issue", ["category"])

    op.create_table(
        "scope_row",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("system", sa.String(24), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("key", sa.String(60), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("proposed_status", sa.String(16), nullable=False),
        sa.Column("document_revision_id", sa.Uuid(), nullable=True),
        sa.Column("clause_id", sa.Uuid(), nullable=True),
        sa.Column("clause_number", sa.String(40), nullable=True),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("edited_by", sa.String(200), nullable=True),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", sa.String(200), nullable=True),
        sa.Column("confirmed_by_id", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        _created(),
        _bid("scope_row"),
        sa.ForeignKeyConstraint(
            ["document_revision_id"],
            ["document_revision.id"],
            name=op.f("fk_scope_row_document_revision_id_document_revision"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["clause_id"],
            ["spec_clause.id"],
            name=op.f("fk_scope_row_clause_id_spec_clause"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scope_row")),
        sa.UniqueConstraint("bid_id", "system", "kind", "key", name=op.f("uq_scope_row")),
        sa.CheckConstraint(
            "status IN ('included', 'excluded', 'by_others', 'unclear')",
            name=op.f("ck_scope_row_status_known"),
        ),
        sa.CheckConstraint(
            "kind IN ('obligation', 'interface')", name=op.f("ck_scope_row_kind_known")
        ),
        sa.CheckConstraint("source IN ('rule', 'person')", name=op.f("ck_scope_row_source_known")),
    )
    op.create_index(op.f("ix_scope_row_bid_id"), "scope_row", ["bid_id"])

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
