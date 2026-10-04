"""Match lines on a sheet's design basis (FR-DSN-06).

* `sheet_design.match_lines`: the match lines found across the plan, each with the words of
  its label, where it runs and the side the sheet answers for.
* `sheet_design.scope_source`: where the scope came from: the match lines, or a person.

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sheet_design",
        sa.Column(
            "match_lines",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("sheet_design", sa.Column("scope_source", sa.String(16), nullable=True))
    # A scope stored before this was set by a person through the API: nothing else set one.
    op.execute("UPDATE sheet_design SET scope_source = 'person' WHERE scope IS NOT NULL")
    op.create_check_constraint(
        op.f("ck_sheet_design_scope_source_known"),
        "sheet_design",
        "scope_source IS NULL OR scope_source IN ('match_lines', 'person')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_sheet_design_scope_source_known"), "sheet_design", type_="check")
    op.drop_column("sheet_design", "scope_source")
    op.drop_column("sheet_design", "match_lines")
