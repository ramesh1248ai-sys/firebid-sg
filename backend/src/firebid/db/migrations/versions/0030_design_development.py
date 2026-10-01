"""Design development for design-intent tenders (P1-12).

`sheet_design`: a sheet's design basis (the criteria its notes state, and the one a person
confirmed) and the layout proposed from it. Bid-scoped, under row-level security.

`pipe_run.origin`: `detected` for a run read from the drawing, `designed` for one the design
rules proposed. Detecting a sheet again replaces only what it detected.

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMPTY_LIST = sa.text("'[]'::jsonb")
EMPTY = sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.add_column(
        "pipe_run",
        sa.Column("origin", sa.String(16), nullable=False, server_default="detected"),
    )
    op.create_check_constraint(
        op.f("ck_pipe_run_origin_known"), "pipe_run", "origin IN ('detected', 'designed')"
    )

    op.create_table(
        "sheet_design",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("sheet_id", sa.Uuid(), nullable=False),
        sa.Column("view_id", sa.Uuid(), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("design_intent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("intent_quote", sa.Text(), nullable=True),
        sa.Column("drawn_heads", sa.Integer(), nullable=False),
        sa.Column("criteria", postgresql.JSONB(), nullable=False, server_default=EMPTY_LIST),
        sa.Column("criterion", postgresql.JSONB(), nullable=True),
        sa.Column("scope", postgresql.JSONB(), nullable=True),
        sa.Column("rule_version", sa.Integer(), nullable=True),
        sa.Column("spaces", postgresql.JSONB(), nullable=False, server_default=EMPTY_LIST),
        sa.Column("totals", postgresql.JSONB(), nullable=False, server_default=EMPTY),
        sa.Column("laid_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by_id", sa.Uuid(), nullable=True),
        sa.Column("confirmed_by", sa.String(200), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_sheet_design_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["sheet_id"],
            ["sheet.id"],
            name=op.f("fk_sheet_design_sheet_id_sheet"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_id"],
            ["app_user.id"],
            name=op.f("fk_sheet_design_confirmed_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sheet_design")),
        sa.UniqueConstraint("sheet_id", name=op.f("uq_sheet_design_sheet_id")),
        sa.CheckConstraint(
            "state IN ('proposed', 'confirmed', 'blocked')",
            name=op.f("ck_sheet_design_state_known"),
        ),
        sa.CheckConstraint(
            "state <> 'confirmed' OR (criterion IS NOT NULL AND confirmed_by IS NOT NULL)",
            name=op.f("ck_sheet_design_confirmation_named"),
        ),
        sa.CheckConstraint(
            "state <> 'blocked' OR note IS NOT NULL",
            name=op.f("ck_sheet_design_blocked_says_why"),
        ),
    )
    op.create_index(op.f("ix_sheet_design_bid_id"), "sheet_design", ["bid_id"])
    op.execute("ALTER TABLE sheet_design ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY firebid_app_bid_scope ON sheet_design FOR ALL TO firebid_app "
        "USING (firebid_can_see_bid(bid_id)) WITH CHECK (firebid_can_see_bid(bid_id))"
    )
    op.execute(
        "CREATE POLICY firebid_service_all ON sheet_design FOR ALL TO firebid_service "
        "USING (true) WITH CHECK (true)"
    )


def downgrade() -> None:
    op.drop_table("sheet_design")
    op.drop_constraint(op.f("ck_pipe_run_origin_known"), "pipe_run", type_="check")
    op.drop_column("pipe_run", "origin")
