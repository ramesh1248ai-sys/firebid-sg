"""Specification clauses and attributes (P1-06): FR-SPEC-01, FR-SPEC-05.

Both are tender records, so both are under row-level security per bid.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BID_SCOPED = ("spec_clause", "spec_attribute")


def upgrade() -> None:
    op.create_table(
        "spec_clause",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("document_revision_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(40), nullable=False),
        sa.Column("heading", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("parent", sa.String(40), nullable=True),
        sa.Column("anchor", postgresql.JSONB(), nullable=False),
        sa.Column("system", sa.String(24), nullable=False),
        sa.Column("system_source", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_spec_clause_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["document_revision_id"],
            ["document_revision.id"],
            name=op.f("fk_spec_clause_document_revision_id_document_revision"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spec_clause")),
        sa.UniqueConstraint(
            "document_revision_id", "ordinal", name=op.f("uq_spec_clause_document_revision_id")
        ),
    )
    for name in ("bid_id", "document_revision_id"):
        op.create_index(op.f(f"ix_spec_clause_{name}"), "spec_clause", [name])

    op.create_table(
        "spec_attribute",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("lineage_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("document_revision_id", sa.Uuid(), nullable=False),
        sa.Column("clause_id", sa.Uuid(), nullable=True),
        sa.Column("clause_number", sa.String(40), nullable=False),
        sa.Column("system", sa.String(24), nullable=False),
        sa.Column("attribute", sa.String(40), nullable=False),
        sa.Column("value", sa.String(200), nullable=False),
        sa.Column("dn_min", sa.Integer(), nullable=True),
        sa.Column("dn_max", sa.Integer(), nullable=True),
        sa.Column("condition", sa.String(200), nullable=True),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("citation_ok", sa.Boolean(), nullable=False),
        sa.Column("citation_reason", sa.Text(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("provenance", postgresql.JSONB(), nullable=False),
        sa.Column("verified_by", sa.String(200), nullable=True),
        sa.Column("verified_by_id", sa.Uuid(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_spec_attribute_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["spec_attribute.id"],
            name=op.f("fk_spec_attribute_supersedes_id_spec_attribute"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["document_revision_id"],
            ["document_revision.id"],
            name=op.f("fk_spec_attribute_document_revision_id_document_revision"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["clause_id"],
            ["spec_clause.id"],
            name=op.f("fk_spec_attribute_clause_id_spec_clause"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f("fk_spec_attribute_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spec_attribute")),
        sa.UniqueConstraint("lineage_id", "version", name=op.f("uq_spec_attribute_lineage_id")),
        sa.CheckConstraint(
            "state IN ('proposed', 'verified', 'rejected')",
            name=op.f("ck_spec_attribute_state_known"),
        ),
        sa.CheckConstraint(
            "method IN ('rule', 'model', 'person')", name=op.f("ck_spec_attribute_method_known")
        ),
        sa.CheckConstraint(
            "state <> 'verified' OR verified_by IS NOT NULL",
            name=op.f("ck_spec_attribute_verifier_named"),
        ),
    )
    for name in ("bid_id", "lineage_id", "document_revision_id", "system"):
        op.create_index(op.f(f"ix_spec_attribute_{name}"), "spec_attribute", [name])

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


def downgrade() -> None:
    op.drop_table("spec_attribute")
    op.drop_table("spec_clause")
