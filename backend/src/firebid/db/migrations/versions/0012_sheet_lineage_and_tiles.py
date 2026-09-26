"""Sheet lineage and the tile pyramid (FR-DOC-07, ADR-005).

`source_ref` is the lineage every quantity measured on a sheet inherits, so it is stored on
the sheet rather than recomputed later from whatever is still to hand.

The pyramid columns describe where a sheet's tiles are. `content_hash` is derived from the
document's digest and the page, which is what lets a re-uploaded set reuse tiles it already
has, and what lets a renderer change invalidate all of them at once.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    ("source_ref", postgresql.JSONB()),
    ("content_hash", sa.String(64)),
    ("base_width_px", sa.Integer()),
    ("base_height_px", sa.Integer()),
    ("max_level", sa.Integer()),
    ("renderer_version", sa.String(16)),
    ("thumbnail_key", sa.String(512)),
)


def upgrade() -> None:
    for name, kind in COLUMNS:
        op.add_column("sheet", sa.Column(name, kind, nullable=True))
    # The tile endpoint looks a sheet up by its content hash on every request.
    op.create_index("ix_sheet_content_hash", "sheet", ["content_hash"])


def downgrade() -> None:
    op.drop_index("ix_sheet_content_hash", table_name="sheet")
    for name, _ in reversed(COLUMNS):
        op.drop_column("sheet", name)
