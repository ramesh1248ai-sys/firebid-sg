"""A sheet's detection fingerprint: detect only what changed (NFR-01, stage caching).

`sheet_geometry.detection_fingerprint`: a digest of everything a sheet's detection was last
made from. Confirming one symbol detects the bid's sheets again; a sheet whose digest is
unchanged keeps its stored detections. Null until the sheet is next detected.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sheet_geometry", sa.Column("detection_fingerprint", sa.String(64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("sheet_geometry", "detection_fingerprint")
