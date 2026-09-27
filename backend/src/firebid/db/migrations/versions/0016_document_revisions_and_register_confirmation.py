"""Document revisions for the specification register, and register confirmation (FR-DOC-03).

A specification, a schedule or the conditions of contract is revised during a tender just as
a drawing is, and the register must hold exactly one Current revision of each. So a document
revision follows the same state machine as a sheet revision (requirements §7 names one model
for both), keyed by the document's identity (`doc_key`: its document number, or failing that
its title) rather than a drawing number. The same partial unique index makes the database
refuse a second Current.

`register_confirmation` is the output of stage S1: the Estimator's statement that the
registers are complete and right. It records who, when, and a hash of what the registers held,
so a later change to them is visible against what was confirmed.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATES = ("received", "registered", "current", "superseded", "withdrawn", "conflict")
BID_SCOPED = ("document_revision", "register_confirmation")


def upgrade() -> None:
    op.create_table(
        "document_revision",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("doc_type", sa.String(32), nullable=True),
        sa.Column("doc_key", sa.String(200), nullable=True),
        sa.Column("title", sa.String(300), nullable=True),
        sa.Column("revision_label", sa.String(40), nullable=True),
        sa.Column("revision_date", sa.Date(), nullable=True),
        sa.Column("state", sa.String(24), nullable=False, server_default="received"),
        sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
        sa.Column("addendum_id", sa.Uuid(), nullable=True),
        sa.Column("sources", postgresql.JSONB(), nullable=True),
        sa.Column("conflict_reason", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_document_revision_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["document.id"],
            name=op.f("fk_document_revision_document_id_document"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["document_revision.id"],
            name=op.f("fk_document_revision_superseded_by_id_document_revision"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["addendum_id"],
            ["addendum.id"],
            name=op.f("fk_document_revision_addendum_id_addendum"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f("fk_document_revision_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_revision")),
        sa.UniqueConstraint("document_id", name=op.f("uq_document_revision_document_id")),
        sa.CheckConstraint(f"state IN {STATES}", name=op.f("ck_document_revision_state_known")),
        sa.CheckConstraint(
            "doc_key IS NOT NULL OR state IN ('received', 'withdrawn')",
            name=op.f("ck_document_revision_identified_unless_received"),
        ),
    )
    op.create_index(op.f("ix_document_revision_bid_id"), "document_revision", ["bid_id"])
    op.create_index(op.f("ix_document_revision_doc_key"), "document_revision", ["doc_key"])
    op.create_index(op.f("ix_document_revision_state"), "document_revision", ["state"])
    op.create_index(
        "uq_document_revision_one_current",
        "document_revision",
        ["bid_id", "doc_key"],
        unique=True,
        postgresql_where=sa.text("state = 'current'"),
    )

    op.create_table(
        "register_confirmation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("confirmed_by_id", sa.Uuid(), nullable=False),
        sa.Column("confirmed_role", sa.String(40), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("drawings", sa.Integer(), nullable=False),
        sa.Column("documents", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"],
            ["bid.id"],
            name=op.f("fk_register_confirmation_bid_id_bid"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_id"],
            ["app_user.id"],
            name=op.f("fk_register_confirmation_confirmed_by_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_register_confirmation")),
    )
    op.create_index(op.f("ix_register_confirmation_bid_id"), "register_confirmation", ["bid_id"])

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
    op.drop_index(op.f("ix_register_confirmation_bid_id"), table_name="register_confirmation")
    op.drop_table("register_confirmation")
    op.drop_index("uq_document_revision_one_current", table_name="document_revision")
    op.drop_index(op.f("ix_document_revision_state"), table_name="document_revision")
    op.drop_index(op.f("ix_document_revision_doc_key"), table_name="document_revision")
    op.drop_index(op.f("ix_document_revision_bid_id"), table_name="document_revision")
    op.drop_table("document_revision")
