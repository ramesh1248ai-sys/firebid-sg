"""The workflow stage a bid has reached (requirements §5, S0-S9).

The lifecycle state says what may happen next; the stage says where the work has got to.
The dashboard shows both.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAGES = tuple(f"S{index}" for index in range(10))


def upgrade() -> None:
    op.add_column("bid", sa.Column("stage", sa.String(4), nullable=False, server_default="S0"))
    op.create_check_constraint("ck_bid_stage_known", "bid", f"stage IN {STAGES}")
    op.create_index("ix_bid_stage", "bid", ["stage"])


def downgrade() -> None:
    op.drop_index("ix_bid_stage", table_name="bid")
    op.drop_constraint("ck_bid_stage_known", "bid", type_="check")
    op.drop_column("bid", "stage")
