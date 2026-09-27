"""Sheet geometry: where each sheet's primitives are, and an index to find them (FR-VIS-01).

The primitives themselves are a Parquet file in object storage, keyed by the sheet's content
hash and the extractor version (the stage cache), because a dense sheet has tens of
thousands of them and they are read as columns. `sheet_geometry` records, per sheet, which
file, how it was extracted, what it holds and how long it took.

`geometry_feature` holds the bounding boxes worth finding by place: text, inserts, circles
and dimensions. They are PostgreSQL `box` values with a GiST index, which answers "what is
near here" without PostGIS; PostGIS is adopted only through an ADR (project-context).

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BID_SCOPED = ("sheet_geometry", "geometry_feature")


def upgrade() -> None:
    op.create_table(
        "sheet_geometry",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("sheet_id", sa.Uuid(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("extractor_version", sa.String(16), nullable=False),
        sa.Column("object_key", sa.String(512), nullable=False),
        sa.Column("method", sa.String(24), nullable=False),
        sa.Column("counts", postgresql.JSONB(), nullable=False),
        sa.Column("page", postgresql.JSONB(), nullable=False),
        sa.Column("views", postgresql.JSONB(), nullable=True),
        sa.Column("seconds", sa.Float(), nullable=True),
        sa.Column("from_cache", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_sheet_geometry_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["sheet_id"],
            ["sheet.id"],
            name=op.f("fk_sheet_geometry_sheet_id_sheet"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sheet_geometry")),
        sa.UniqueConstraint("sheet_id", name=op.f("uq_sheet_geometry_sheet_id")),
    )
    op.create_index(op.f("ix_sheet_geometry_bid_id"), "sheet_geometry", ["bid_id"])

    op.create_table(
        "geometry_feature",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("sheet_id", sa.Uuid(), nullable=False),
        sa.Column("row", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("layer", sa.String(120), nullable=True),
        sa.Column("label", sa.String(300), nullable=True),
        sa.ForeignKeyConstraint(
            ["bid_id"],
            ["bid.id"],
            name=op.f("fk_geometry_feature_bid_id_bid"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sheet_id"],
            ["sheet.id"],
            name=op.f("fk_geometry_feature_sheet_id_sheet"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_geometry_feature")),
    )
    # PostgreSQL's native box, which SQLAlchemy has no built-in type for.
    op.execute("ALTER TABLE geometry_feature ADD COLUMN bbox box NOT NULL")
    op.create_index(op.f("ix_geometry_feature_sheet_id"), "geometry_feature", ["sheet_id"])
    op.execute("CREATE INDEX ix_geometry_feature_bbox ON geometry_feature USING gist (bbox)")

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
    op.execute("DROP INDEX IF EXISTS ix_geometry_feature_bbox")
    op.drop_index(op.f("ix_geometry_feature_sheet_id"), table_name="geometry_feature")
    op.drop_table("geometry_feature")
    op.drop_index(op.f("ix_sheet_geometry_bid_id"), table_name="sheet_geometry")
    op.drop_table("sheet_geometry")
