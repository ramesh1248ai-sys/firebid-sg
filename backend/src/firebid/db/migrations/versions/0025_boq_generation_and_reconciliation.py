"""BOQ generation and client BOQ reconciliation (P1-09).

* `boq_template`: the organisation's versioned BOQ templates (FR-ADM-03).
* `measurement_convention`: a tender's measurement conventions (FR-BOQ-06), under
  row-level security per bid.
* `boq` and `boq_line`: the template version, current flag, a stable line key, the group,
  level and allowance, and the reason an untraced line is marked (FR-BOQ-05).
* `client_boq`, `client_boq_line`, `client_boq_mapping`: reading status and the model's
  column proposal; line kind and the cells a priced copy writes; mapping state, method,
  stable line key, reason, the variance flag and provenance.

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "boq_template",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_boq_template_organisation_id_organisation"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f("fk_boq_template_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_boq_template")),
        sa.UniqueConstraint("organisation_id", "key", "version", name="uq_boq_template_version"),
    )
    op.create_index(op.f("ix_boq_template_organisation_id"), "boq_template", ["organisation_id"])

    op.create_table(
        "measurement_convention",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("settings", postgresql.JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["bid_id"],
            ["bid.id"],
            name=op.f("fk_measurement_convention_bid_id_bid"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f("fk_measurement_convention_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_measurement_convention")),
    )
    op.create_index(op.f("ix_measurement_convention_bid_id"), "measurement_convention", ["bid_id"])
    op.execute("ALTER TABLE measurement_convention ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY firebid_app_bid_scope ON measurement_convention FOR ALL TO firebid_app "
        "USING (firebid_can_see_bid(bid_id)) WITH CHECK (firebid_can_see_bid(bid_id))"
    )
    op.execute(
        "CREATE POLICY firebid_service_all ON measurement_convention FOR ALL TO firebid_service "
        "USING (true) WITH CHECK (true)"
    )

    op.add_column("boq", sa.Column("template_version", sa.Integer(), nullable=True))
    op.add_column(
        "boq",
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )

    op.add_column("boq_line", sa.Column("line_key", sa.String(64), nullable=True))
    op.add_column("boq_line", sa.Column("group_heading", sa.String(200), nullable=True))
    op.add_column("boq_line", sa.Column("level", sa.String(40), nullable=True))
    op.add_column("boq_line", sa.Column("allowance_percent", sa.Numeric(6, 3), nullable=True))
    op.add_column("boq_line", sa.Column("marker_note", sa.Text(), nullable=True))
    op.create_index(op.f("ix_boq_line_line_key"), "boq_line", ["line_key"])

    op.add_column(
        "client_boq",
        sa.Column("status", sa.String(16), nullable=False, server_default="read"),
    )
    op.add_column("client_boq", sa.Column("proposal", postgresql.JSONB(), nullable=True))
    op.add_column("client_boq", sa.Column("reason", sa.Text(), nullable=True))
    op.add_column("client_boq", sa.Column("document_revision_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_client_boq_document_revision_id"), "client_boq", ["document_revision_id"]
    )
    op.create_check_constraint(
        op.f("ck_client_boq_status_known"),
        "client_boq",
        "status IN ('read', 'needs_columns', 'failed')",
    )

    op.add_column(
        "client_boq_line",
        sa.Column("kind", sa.String(16), nullable=False, server_default="line"),
    )
    op.add_column("client_boq_line", sa.Column("rate_cell", sa.String(12), nullable=True))
    op.add_column("client_boq_line", sa.Column("amount_cell", sa.String(12), nullable=True))
    op.create_check_constraint(
        op.f("ck_client_boq_line_kind_known"),
        "client_boq_line",
        "kind IN ('line', 'provisional', 'lump_sum')",
    )

    op.add_column(
        "client_boq_mapping",
        sa.Column("state", sa.String(16), nullable=False, server_default="proposed"),
    )
    op.add_column(
        "client_boq_mapping",
        sa.Column("method", sa.String(16), nullable=False, server_default="rule"),
    )
    op.add_column("client_boq_mapping", sa.Column("boq_line_key", sa.String(64), nullable=True))
    op.add_column("client_boq_mapping", sa.Column("reason", sa.Text(), nullable=True))
    op.add_column(
        "client_boq_mapping",
        sa.Column("flagged", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "client_boq_mapping",
        sa.Column("provenance", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_check_constraint(
        op.f("ck_client_boq_mapping_state_known"),
        "client_boq_mapping",
        "state IN ('proposed', 'confirmed', 'rejected')",
    )
    op.create_check_constraint(
        op.f("ck_client_boq_mapping_method_known"),
        "client_boq_mapping",
        "method IN ('rule', 'model', 'person')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_client_boq_mapping_method_known"), "client_boq_mapping", type_="check"
    )
    op.drop_constraint(
        op.f("ck_client_boq_mapping_state_known"), "client_boq_mapping", type_="check"
    )
    for column in ("provenance", "flagged", "reason", "boq_line_key", "method", "state"):
        op.drop_column("client_boq_mapping", column)
    op.drop_constraint(op.f("ck_client_boq_line_kind_known"), "client_boq_line", type_="check")
    for column in ("amount_cell", "rate_cell", "kind"):
        op.drop_column("client_boq_line", column)
    op.drop_constraint(op.f("ck_client_boq_status_known"), "client_boq", type_="check")
    op.drop_index(op.f("ix_client_boq_document_revision_id"), table_name="client_boq")
    for column in ("document_revision_id", "reason", "proposal", "status"):
        op.drop_column("client_boq", column)
    op.drop_index(op.f("ix_boq_line_line_key"), table_name="boq_line")
    for column in ("marker_note", "allowance_percent", "level", "group_heading", "line_key"):
        op.drop_column("boq_line", column)
    op.drop_column("boq", "is_current")
    op.drop_column("boq", "template_version")
    op.drop_table("measurement_convention")
    op.drop_table("boq_template")
