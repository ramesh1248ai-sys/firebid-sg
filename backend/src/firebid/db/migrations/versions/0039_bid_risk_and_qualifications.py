"""Bid risk and qualifications (P2-07).

* `scope_check`, `risk`: the scope-gap checklist and the risk register, under row-level
  security.
* `qualification`: every entry now names its source (`source_kind`, `source_ref`), and may
  be an exclusion or a deviation as well as a qualification or assumption. Entries proposed
  from clarifications before this step are given their clarification as their source.

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-04
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMPTY = sa.text("'{}'::jsonb")
EMPTY_LIST = sa.text("'[]'::jsonb")
TABLES = ("scope_check", "risk")


def _common(table: str) -> list[Any]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f(f"fk_{table}_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
    ]


def upgrade() -> None:
    op.create_table(
        "scope_check",
        *_common("scope_check"),
        sa.Column("system", sa.String(24), nullable=False),
        sa.Column("item_key", sa.String(60), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("proposed_status", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("basis", sa.Text(), nullable=False, server_default=""),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default=EMPTY_LIST),
        sa.Column("decided_by", sa.String(200), nullable=True),
        sa.Column("decided_by_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.UniqueConstraint("bid_id", "system", "item_key", name=op.f("uq_scope_check")),
        sa.CheckConstraint(
            "status IN ('open', 'included', 'excluded', 'by_others', 'clarified')",
            name=op.f("ck_scope_check_status_known"),
        ),
        sa.CheckConstraint(
            "proposed_status IN ('open', 'included', 'excluded', 'by_others', 'clarified')",
            name=op.f("ck_scope_check_proposed_known"),
        ),
        sa.CheckConstraint(
            "decided_by IS NULL OR status <> 'open'", name=op.f("ck_scope_check_decision_resolves")
        ),
    )
    op.create_index(op.f("ix_scope_check_bid_id"), "scope_check", ["bid_id"])

    op.create_table(
        "risk",
        *_common("risk"),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("level", sa.String(40), nullable=True),
        sa.Column("multiplier", sa.String(60), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default=EMPTY),
        sa.Column("rules_version", sa.String(40), nullable=False),
        sa.Column("last_found_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("proposed_treatment", sa.String(16), nullable=False),
        sa.Column("treatment", sa.String(16), nullable=True),
        sa.Column("owner", sa.String(200), nullable=True),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("impact", postgresql.JSONB(), nullable=False, server_default=EMPTY),
        sa.Column("impact_state", sa.String(16), nullable=False),
        sa.Column("cost_allowance", sa.Numeric(14, 2), nullable=True),
        sa.Column("programme_hours", sa.Numeric(12, 2), nullable=True),
        sa.Column("impact_reason", sa.Text(), nullable=True),
        sa.Column("impact_by", sa.String(200), nullable=True),
        sa.Column("impact_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("bid_id", "key", name=op.f("uq_risk_key")),
        sa.CheckConstraint(
            "category IN ('design_responsibility', 'execution')",
            name=op.f("ck_risk_category_known"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'treated', 'closed', 'no_longer_found')",
            name=op.f("ck_risk_status_known"),
        ),
        sa.CheckConstraint(
            "treatment IS NULL OR treatment IN ('price', 'qualify', 'clarify', 'accept')",
            name=op.f("ck_risk_treatment_known"),
        ),
        sa.CheckConstraint(
            "impact_state IN ('computed', 'accepted', 'adjusted')",
            name=op.f("ck_risk_impact_state_known"),
        ),
        sa.CheckConstraint("jsonb_array_length(evidence) >= 1", name=op.f("ck_risk_has_evidence")),
        sa.CheckConstraint(
            "impact_state <> 'adjusted' OR btrim(coalesce(impact_reason, '')) <> ''",
            name=op.f("ck_risk_adjustment_reasoned"),
        ),
    )
    op.create_index(op.f("ix_risk_bid_id"), "risk", ["bid_id"])
    op.create_index(op.f("ix_risk_category"), "risk", ["category"])

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

    op.add_column("qualification", sa.Column("source_kind", sa.String(24), nullable=True))
    op.add_column("qualification", sa.Column("source_ref", sa.String(200), nullable=True))
    op.add_column(
        "qualification",
        sa.Column("source_label", sa.String(300), nullable=False, server_default=""),
    )
    op.execute(
        "UPDATE qualification SET source_kind = 'clarification', "
        "source_ref = coalesce(clarification_id::text, id::text)"
    )
    op.alter_column("qualification", "source_kind", nullable=False)
    op.alter_column("qualification", "source_ref", nullable=False)
    op.drop_constraint(op.f("ck_qualification_kind_known"), "qualification", type_="check")
    op.create_check_constraint(
        op.f("ck_qualification_kind_known"),
        "qualification",
        "kind IN ('qualification', 'assumption', 'exclusion', 'deviation')",
    )
    op.create_check_constraint(
        op.f("ck_qualification_source_named"), "qualification", "btrim(source_ref) <> ''"
    )
    op.create_unique_constraint(
        op.f("uq_qualification_source"), "qualification", ["bid_id", "source_kind", "source_ref"]
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_qualification_source"), "qualification", type_="unique")
    op.drop_constraint(op.f("ck_qualification_source_named"), "qualification", type_="check")
    op.execute("DELETE FROM qualification WHERE kind IN ('exclusion', 'deviation')")
    op.drop_constraint(op.f("ck_qualification_kind_known"), "qualification", type_="check")
    op.create_check_constraint(
        op.f("ck_qualification_kind_known"),
        "qualification",
        "kind IN ('qualification', 'assumption')",
    )
    op.drop_column("qualification", "source_label")
    op.drop_column("qualification", "source_ref")
    op.drop_column("qualification", "source_kind")
    for table in reversed(TABLES):
        op.drop_table(table)
