"""Symbols: the object library, consultant mappings, legend rows and instances (P1-04).

`object_type` and `symbol_mapping` are organisation-level libraries, versioned by new rows
(FR-ADM-02); like `title_block_layout` they hold no tender content and are filtered by
organisation in the application. `legend_entry` and `symbol_instance` are bid records under
row-level security.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-28
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BID_SCOPED = ("legend_entry", "symbol_instance")


def _created(table: str) -> list[Any]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f(f"fk_{table}_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "object_type",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("measure", sa.String(16), nullable=False),
        sa.Column("attribute_schema", postgresql.JSONB(), nullable=False),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("changed_by", sa.String(200), nullable=True),
        *_created("object_type"),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_object_type_organisation_id_organisation"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["object_type.id"],
            name=op.f("fk_object_type_supersedes_id_object_type"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_object_type")),
        sa.UniqueConstraint(
            "organisation_id", "key", "version", name=op.f("uq_object_type_organisation_id")
        ),
    )
    op.create_index(op.f("ix_object_type_organisation_id"), "object_type", ["organisation_id"])

    op.create_table(
        "symbol_mapping",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("lineage_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("consultant_key", sa.String(200), nullable=False),
        sa.Column("consultant", sa.String(200), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("signature", postgresql.JSONB(), nullable=False),
        sa.Column("description", sa.String(300), nullable=True),
        sa.Column("object_type_key", sa.String(80), nullable=True),
        sa.Column("attributes", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("provenance", postgresql.JSONB(), nullable=False),
        sa.Column("confirmed_by", sa.String(200), nullable=True),
        sa.Column("confirmed_by_id", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("change_note", sa.Text(), nullable=True),
        *_created("symbol_mapping"),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_symbol_mapping_organisation_id_organisation"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["symbol_mapping.id"],
            name=op.f("fk_symbol_mapping_supersedes_id_symbol_mapping"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_symbol_mapping_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_symbol_mapping")),
        sa.UniqueConstraint("lineage_id", "version", name=op.f("uq_symbol_mapping_lineage_id")),
        sa.CheckConstraint(
            "state IN ('proposed', 'confirmed', 'rejected')",
            name=op.f("ck_symbol_mapping_state_known"),
        ),
        sa.CheckConstraint(
            "source IN ('rule', 'model', 'reuse', 'person')",
            name=op.f("ck_symbol_mapping_source_known"),
        ),
        sa.CheckConstraint(
            "state <> 'confirmed' OR confirmed_by IS NOT NULL",
            name=op.f("ck_symbol_mapping_confirmation_named"),
        ),
    )
    for column in ("organisation_id", "lineage_id", "consultant_key", "project_id"):
        op.create_index(op.f(f"ix_symbol_mapping_{column}"), "symbol_mapping", [column])

    op.create_table(
        "legend_entry",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("sheet_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(80), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("symbol_box", postgresql.JSONB(), nullable=False),
        sa.Column("row_box", postgresql.JSONB(), nullable=False),
        sa.Column("signature", postgresql.JSONB(), nullable=False),
        sa.Column("crop_key", sa.String(512), nullable=True),
        sa.Column("mapping_lineage_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_legend_entry_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["sheet_id"],
            ["sheet.id"],
            name=op.f("fk_legend_entry_sheet_id_sheet"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_legend_entry")),
        sa.UniqueConstraint("sheet_id", "ordinal", name=op.f("uq_legend_entry_sheet_id")),
    )
    for column in ("bid_id", "sheet_id", "mapping_lineage_id"):
        op.create_index(op.f(f"ix_legend_entry_{column}"), "legend_entry", [column])

    op.create_table(
        "symbol_instance",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("sheet_id", sa.Uuid(), nullable=False),
        sa.Column("view_id", sa.Uuid(), nullable=True),
        sa.Column("symbol_key", sa.String(120), nullable=False),
        sa.Column("legend_entry_id", sa.Uuid(), nullable=True),
        sa.Column("mapping_lineage_id", sa.Uuid(), nullable=True),
        sa.Column("block", sa.String(200), nullable=True),
        sa.Column("cx", sa.Float(), nullable=False),
        sa.Column("cy", sa.Float(), nullable=False),
        sa.Column("bbox", postgresql.JSONB(), nullable=False),
        sa.Column("rotation", sa.Float(), nullable=True),
        sa.Column("scale", sa.Float(), nullable=True),
        sa.Column("match_distance", sa.Float(), nullable=True),
        sa.Column("detector_version", sa.String(16), nullable=False),
        sa.ForeignKeyConstraint(
            ["bid_id"],
            ["bid.id"],
            name=op.f("fk_symbol_instance_bid_id_bid"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sheet_id"],
            ["sheet.id"],
            name=op.f("fk_symbol_instance_sheet_id_sheet"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_symbol_instance")),
    )
    for column in ("bid_id", "sheet_id", "symbol_key", "mapping_lineage_id"):
        op.create_index(op.f(f"ix_symbol_instance_{column}"), "symbol_instance", [column])

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
    op.drop_table("symbol_instance")
    op.drop_table("legend_entry")
    op.drop_table("symbol_mapping")
    op.drop_table("object_type")
