"""Domain model, audit trail, partitions and the restricted application role.

Partitioning is created here, before the tables hold data: hash partitions by bid_id for the
takeoff tables, monthly range partitions for audit_event.

"Append-only" is enforced by the database, not by convention: the application connects as
`firebid_app`, which has no UPDATE or DELETE on the audit tables, and a trigger refuses both.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-22
"""

import os
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from psycopg import sql
from sqlalchemy.dialects import postgresql

import firebid.db.types  # custom column types used below

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "firebid_app"
HASH_PARTITIONS = 8
PARTITIONED_BY_BID = ("detected_object", "qto_item", "evidence")
APPEND_ONLY_TABLES = ("audit_event", "audit_chain_link")

# Creates a month's partition if it is missing. SECURITY DEFINER, so the application role can
# call it (through the scheduled job) without holding CREATE on the schema.
ENSURE_AUDIT_PARTITION = """
CREATE OR REPLACE FUNCTION ensure_audit_event_partition(target date)
RETURNS text AS $$
DECLARE
    start_date date := date_trunc('month', target)::date;
    end_date   date := (date_trunc('month', target) + interval '1 month')::date;
    part_name  text := format('audit_event_%s', to_char(start_date, 'YYYYMM'));
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = part_name) THEN
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF audit_event FOR VALUES FROM (%L) TO (%L)',
            part_name, start_date, end_date);
    END IF;
    RETURN part_name;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
"""

REJECT_WRITE = """
CREATE OR REPLACE FUNCTION firebid_reject_write() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'table % is append-only; % is not allowed', TG_TABLE_NAME, TG_OP
        USING ERRCODE = '42501';
END;
$$ LANGUAGE plpgsql;
"""


def _app_role_password() -> str:
    return os.environ.get("FIREBID_APP_DB_PASSWORD", "firebid-app")


def _create_partitions() -> None:
    for table in PARTITIONED_BY_BID:
        for remainder in range(HASH_PARTITIONS):
            op.execute(
                f"CREATE TABLE {table}_p{remainder} PARTITION OF {table} "
                f"FOR VALUES WITH (MODULUS {HASH_PARTITIONS}, REMAINDER {remainder})"
            )
    op.execute(ENSURE_AUDIT_PARTITION)
    # Last month, this month and the next two, so inserts never land without a partition.
    for offset in (-1, 0, 1, 2):
        op.execute(
            "SELECT ensure_audit_event_partition("
            f"(date_trunc('month', current_date) + interval '{offset} month')::date)"
        )


def _make_append_only() -> None:
    op.execute(REJECT_WRITE)
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION firebid_reject_write()"
        )


def _driver_connection() -> Any:
    """The psycopg connection under Alembic's bind, for safely quoted role statements."""
    connection = op.get_bind().connection.driver_connection
    if connection is None:
        raise RuntimeError("no database connection")
    return connection


def _create_app_role() -> None:
    """The role the application connects as: no rights to change history, no BYPASSRLS."""
    connection = _driver_connection()
    role = sql.Identifier(APP_ROLE)
    connection.execute(
        sql.SQL(
            "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {name}) "
            "THEN CREATE ROLE {role} LOGIN; END IF; END $$;"
        ).format(name=sql.Literal(APP_ROLE), role=role)
    )
    connection.execute(
        sql.SQL("ALTER ROLE {role} WITH LOGIN PASSWORD {password} NOBYPASSRLS").format(
            role=role, password=sql.Literal(_app_role_password())
        )
    )
    statements = [
        "GRANT USAGE ON SCHEMA public TO {role}",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}",
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}",
        "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {role}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {role}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO {role}",
        # History is append-only, so the application may add to it but never change it.
        "REVOKE UPDATE, DELETE ON audit_event, audit_chain_link FROM {role}",
    ]
    for statement in statements:
        connection.execute(sql.SQL(statement).format(role=role))


def _drop_app_role() -> None:
    connection = _driver_connection()
    role = sql.Identifier(APP_ROLE)
    connection.execute(
        sql.SQL(
            "DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname = {name}) THEN "
            "EXECUTE 'DROP OWNED BY {role_literal}'; END IF; END $$;"
        ).format(name=sql.Literal(APP_ROLE), role_literal=sql.SQL(APP_ROLE))
    )
    connection.execute(sql.SQL("DROP ROLE IF EXISTS {role}").format(role=role))


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "audit_chain_link",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chain_key", sa.Uuid(), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=True),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("events_hash", sa.String(length=64), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=True),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("tx_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_chain_link")),
        sa.UniqueConstraint("chain_key", "seq", name="uq_chain_seq"),
    )
    op.create_index(
        op.f("ix_audit_chain_link_chain_key"), "audit_chain_link", ["chain_key"], unique=False
    )
    op.create_table(
        "audit_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=True),
        sa.Column("chain_key", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("actor_label", sa.String(length=200), nullable=False),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.String(length=80), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("tx_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", "occurred_at", name=op.f("pk_audit_event")),
        postgresql_partition_by="RANGE (occurred_at)",
    )
    op.create_index("ix_audit_event_actor", "audit_event", ["actor_id"], unique=False)
    op.create_index(
        "ix_audit_event_bid_occurred", "audit_event", ["bid_id", "occurred_at"], unique=False
    )
    op.create_index(
        "ix_audit_event_entity", "audit_event", ["entity_type", "entity_id"], unique=False
    )
    op.create_table(
        "id_counter",
        sa.Column("scope", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("value", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("scope", "name", name=op.f("pk_id_counter")),
    )
    op.create_table(
        "organisation",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organisation")),
        sa.UniqueConstraint("name", name=op.f("uq_organisation_name")),
    )
    op.create_table(
        "app_user",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=256), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_app_user_organisation_id_organisation"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_user")),
        sa.UniqueConstraint("external_id", name=op.f("uq_app_user_external_id")),
    )
    op.create_index(
        op.f("ix_app_user_organisation_id"), "app_user", ["organisation_id"], unique=False
    )
    op.create_table(
        "audit_retention",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("retain_years", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_audit_retention_organisation_id_organisation"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("organisation_id", name=op.f("pk_audit_retention")),
    )
    op.create_table(
        "measurement_rule",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_note", sa.Text(), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f("fk_measurement_rule_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_measurement_rule_organisation_id_organisation"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_measurement_rule")),
        sa.UniqueConstraint("organisation_id", "key", "version", name="uq_rule_version"),
    )
    op.create_index(
        op.f("ix_measurement_rule_organisation_id"),
        "measurement_rule",
        ["organisation_id"],
        unique=False,
    )
    op.create_table(
        "project",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("developer", sa.String(length=200), nullable=True),
        sa.Column("consultant", sa.String(length=200), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f("fk_project_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_project_organisation_id_organisation"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project")),
    )
    op.create_index(
        op.f("ix_project_organisation_id"), "project", ["organisation_id"], unique=False
    )
    op.create_table(
        "rate",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("item_key", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("unit_rate", firebid.db.types.MoneyType(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("source_type", sa.String(length=24), nullable=False),
        sa.Column("source_reference", sa.String(length=200), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "source_type IN ('company_standard', 'purchase_order', 'quotation')",
            name=op.f("ck_rate_source_known"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f("fk_rate_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_rate_organisation_id_organisation"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["rate.id"],
            name=op.f("fk_rate_supersedes_id_rate"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rate")),
    )
    op.create_index(op.f("ix_rate_item_key"), "rate", ["item_key"], unique=False)
    op.create_index(op.f("ix_rate_organisation_id"), "rate", ["organisation_id"], unique=False)
    op.create_table(
        "user_role",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "role IN ('estimator', 'senior_estimator', 'bid_manager', 'design_manager', 'commercial_director', 'procurement', 'project_manager', 'system_admin', 'executive_sponsor', 'system')",
            name=op.f("ck_user_role_role_known"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_user_role_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "role", name=op.f("pk_user_role")),
    )
    op.create_table(
        "bid",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("human_id", sa.String(length=20), nullable=False),
        sa.Column("client_name", sa.String(length=200), nullable=False),
        sa.Column("tender_reference", sa.String(length=120), nullable=False),
        sa.Column("submission_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("clarification_cutoff", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tender_validity_days", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "state IN ('registered', 'qualifying', 'in_preparation', 'under_review', 'approved_for_submission', 'submitted', 'post_submission_clarification', 'awarded', 'lost', 'withdrawn', 'no_bid')",
            name=op.f("ck_bid_state_known"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f("fk_bid_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_bid_organisation_id_organisation"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_bid_project_id_project"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bid")),
        sa.UniqueConstraint("organisation_id", "human_id", name="uq_bid_human_id"),
    )
    op.create_index(op.f("ix_bid_organisation_id"), "bid", ["organisation_id"], unique=False)
    op.create_index(op.f("ix_bid_project_id"), "bid", ["project_id"], unique=False)
    op.create_index(op.f("ix_bid_state"), "bid", ["state"], unique=False)
    op.create_table(
        "agent_run",
        sa.Column("bid_id", sa.Uuid(), nullable=True),
        sa.Column("route", sa.String(length=80), nullable=False),
        sa.Column("agent", sa.String(length=80), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("config_version", sa.String(length=64), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("cost_sgd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("emulated_capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_type", sa.String(length=80), nullable=True),
        sa.Column("output_ref", sa.String(length=512), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('running', 'succeeded', 'failed', 'escalated', 'cancelled')",
            name=op.f("ck_agent_run_state_known"),
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_agent_run_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_run")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_agent_run_idempotency_key")),
    )
    op.create_index(op.f("ix_agent_run_bid_id"), "agent_run", ["bid_id"], unique=False)
    op.create_index(op.f("ix_agent_run_input_hash"), "agent_run", ["input_hash"], unique=False)
    op.create_index(op.f("ix_agent_run_route"), "agent_run", ["route"], unique=False)
    op.create_table(
        "approval",
        sa.Column("gate", sa.String(length=4), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("approver_id", sa.Uuid(), nullable=False),
        sa.Column("approver_role", sa.String(length=40), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('approved', 'refused')", name=op.f("ck_approval_decision_known")
        ),
        sa.CheckConstraint(
            "gate IN ('G0', 'G1', 'G2', 'G3', 'G4')", name=op.f("ck_approval_gate_known")
        ),
        sa.ForeignKeyConstraint(
            ["approver_id"],
            ["app_user.id"],
            name=op.f("fk_approval_approver_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_approval_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval")),
    )
    op.create_index(op.f("ix_approval_bid_id"), "approval", ["bid_id"], unique=False)
    op.create_index(op.f("ix_approval_gate"), "approval", ["gate"], unique=False)
    op.create_table(
        "bid_member",
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('estimator', 'senior_estimator', 'bid_manager', 'design_manager', 'commercial_director', 'procurement', 'project_manager', 'system_admin', 'executive_sponsor', 'system')",
            name=op.f("ck_bid_member_role_known"),
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_bid_member_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_bid_member_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("bid_id", "user_id", name=op.f("pk_bid_member")),
    )
    op.create_index(op.f("ix_bid_member_user_id"), "bid_member", ["user_id"], unique=False)
    op.create_table(
        "boq",
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("template_key", sa.String(length=80), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint("kind IN ('company', 'client_priced')", name=op.f("ck_boq_kind_known")),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_boq_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f("fk_boq_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_boq")),
    )
    op.create_index(op.f("ix_boq_bid_id"), "boq", ["bid_id"], unique=False)
    op.create_table(
        "detected_object",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("sheet_id", sa.Uuid(), nullable=False),
        sa.Column("sheet_revision_id", sa.Uuid(), nullable=True),
        sa.Column("object_type", sa.String(length=80), nullable=False),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("geometry_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("extraction_method", sa.String(length=24), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("view_id", sa.Uuid(), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_detected_object_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", "bid_id", name=op.f("pk_detected_object")),
        postgresql_partition_by="HASH (bid_id)",
    )
    op.create_index(
        op.f("ix_detected_object_object_type"), "detected_object", ["object_type"], unique=False
    )
    op.create_index(
        op.f("ix_detected_object_sheet_id"), "detected_object", ["sheet_id"], unique=False
    )
    op.create_index(
        op.f("ix_detected_object_sheet_revision_id"),
        "detected_object",
        ["sheet_revision_id"],
        unique=False,
    )
    op.create_table(
        "human_task",
        sa.Column("bid_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
        sa.Column("required_role", sa.String(length=40), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("continuation_task", sa.String(length=120), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('open', 'in_progress', 'done', 'cancelled')",
            name=op.f("ck_human_task_state_known"),
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["app_user.id"],
            name=op.f("fk_human_task_assignee_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_human_task_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["completed_by_id"],
            ["app_user.id"],
            name=op.f("fk_human_task_completed_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_human_task")),
    )
    op.create_index(op.f("ix_human_task_assignee_id"), "human_task", ["assignee_id"], unique=False)
    op.create_index(op.f("ix_human_task_bid_id"), "human_task", ["bid_id"], unique=False)
    op.create_index(op.f("ix_human_task_kind"), "human_task", ["kind"], unique=False)
    op.create_index(op.f("ix_human_task_state"), "human_task", ["state"], unique=False)
    op.create_table(
        "qto_item",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("human_id", sa.String(length=20), nullable=False),
        sa.Column("item_type", sa.String(length=80), nullable=False),
        sa.Column("classification", sa.String(length=80), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("net_quantity", sa.Numeric(precision=16, scale=3), nullable=False),
        sa.Column("allowance_percent", sa.Numeric(precision=6, scale=3), nullable=True),
        sa.Column("length", firebid.db.types.LengthMmType(), nullable=True),
        sa.Column("level", sa.String(length=40), nullable=True),
        sa.Column("zone", sa.String(length=40), nullable=True),
        sa.Column("grid_from", sa.String(length=40), nullable=True),
        sa.Column("grid_to", sa.String(length=40), nullable=True),
        sa.Column("calculation_method", sa.String(length=32), nullable=False),
        sa.Column("rule_key", sa.String(length=80), nullable=True),
        sa.Column("rule_version", sa.Integer(), nullable=True),
        sa.Column("is_manual", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("duplicate_group_id", sa.Uuid(), nullable=True),
        sa.Column("verified_by_id", sa.Uuid(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason_code", sa.String(length=40), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "state IN ('detected', 'proposed', 'verified', 'edited', 'rejected', 'baselined', 'superseded')",
            name=op.f("ck_qto_item_state_known"),
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_qto_item_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f("fk_qto_item_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id", "bid_id"],
            ["qto_item.id", "qto_item.bid_id"],
            name="fk_qto_item_supersedes",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by_id"],
            ["app_user.id"],
            name=op.f("fk_qto_item_verified_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", "bid_id", name=op.f("pk_qto_item")),
        sa.UniqueConstraint("bid_id", "human_id", name="uq_qto_item_human_id"),
        postgresql_partition_by="HASH (bid_id)",
    )
    op.create_index(
        op.f("ix_qto_item_duplicate_group_id"), "qto_item", ["duplicate_group_id"], unique=False
    )
    op.create_index(op.f("ix_qto_item_item_type"), "qto_item", ["item_type"], unique=False)
    op.create_index(op.f("ix_qto_item_level"), "qto_item", ["level"], unique=False)
    op.create_index(op.f("ix_qto_item_state"), "qto_item", ["state"], unique=False)
    op.create_table(
        "tender_package",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_tender_package_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f("fk_tender_package_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tender_package")),
    )
    op.create_index(op.f("ix_tender_package_bid_id"), "tender_package", ["bid_id"], unique=False)
    op.create_table(
        "addendum",
        sa.Column("number", sa.String(length=40), nullable=False),
        sa.Column("issued_on", sa.Date(), nullable=True),
        sa.Column("tender_package_id", sa.Uuid(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_addendum_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f("fk_addendum_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tender_package_id"],
            ["tender_package.id"],
            name=op.f("fk_addendum_tender_package_id_tender_package"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_addendum")),
        sa.UniqueConstraint("bid_id", "number", name="uq_addendum_number"),
    )
    op.create_index(op.f("ix_addendum_bid_id"), "addendum", ["bid_id"], unique=False)
    op.create_table(
        "boq_line",
        sa.Column("boq_id", sa.Uuid(), nullable=False),
        sa.Column("section", sa.String(length=200), nullable=True),
        sa.Column("item_no", sa.String(length=40), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=16, scale=3), nullable=False),
        sa.Column("unit_rate", firebid.db.types.MoneyType(precision=14, scale=2), nullable=True),
        sa.Column("amount", firebid.db.types.MoneyType(precision=14, scale=2), nullable=True),
        sa.Column("rate_id", sa.Uuid(), nullable=True),
        sa.Column("is_provisional", sa.Boolean(), nullable=False),
        sa.Column("is_lump_sum", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_boq_line_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["boq_id"], ["boq.id"], name=op.f("fk_boq_line_boq_id_boq"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["rate_id"], ["rate.id"], name=op.f("fk_boq_line_rate_id_rate"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_boq_line")),
    )
    op.create_index(op.f("ix_boq_line_bid_id"), "boq_line", ["bid_id"], unique=False)
    op.create_index(op.f("ix_boq_line_boq_id"), "boq_line", ["boq_id"], unique=False)
    op.create_index(op.f("ix_boq_line_rate_id"), "boq_line", ["rate_id"], unique=False)
    op.create_table(
        "document",
        sa.Column("tender_package_id", sa.Uuid(), nullable=True),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("media_type", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
        sa.Column("derived_from_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "state IN ('received', 'awaiting_scan', 'quarantined', 'processing', 'done', 'rejected')",
            name=op.f("ck_document_state_known"),
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_document_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f("fk_document_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["derived_from_id"],
            ["document.id"],
            name=op.f("fk_document_derived_from_id_document"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tender_package_id"],
            ["tender_package.id"],
            name=op.f("fk_document_tender_package_id_tender_package"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document")),
        sa.UniqueConstraint("bid_id", "sha256", name="uq_document_bid_sha256"),
    )
    op.create_index(op.f("ix_document_bid_id"), "document", ["bid_id"], unique=False)
    op.create_index(op.f("ix_document_kind"), "document", ["kind"], unique=False)
    op.create_index(op.f("ix_document_state"), "document", ["state"], unique=False)
    op.create_index(
        op.f("ix_document_tender_package_id"), "document", ["tender_package_id"], unique=False
    )
    op.create_table(
        "evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("qto_item_id", sa.Uuid(), nullable=False),
        sa.Column("record", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("missing_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_evidence_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["qto_item_id", "bid_id"],
            ["qto_item.id", "qto_item.bid_id"],
            name="fk_evidence_qto_item",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", "bid_id", name=op.f("pk_evidence")),
        postgresql_partition_by="HASH (bid_id)",
    )
    op.create_index(op.f("ix_evidence_qto_item_id"), "evidence", ["qto_item_id"], unique=False)
    op.create_table(
        "boq_line_source",
        sa.Column("boq_line_id", sa.Uuid(), nullable=False),
        sa.Column("qto_item_id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_boq_line_source_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["boq_line_id"],
            ["boq_line.id"],
            name=op.f("fk_boq_line_source_boq_line_id_boq_line"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("boq_line_id", "qto_item_id", name=op.f("pk_boq_line_source")),
    )
    op.create_index(op.f("ix_boq_line_source_bid_id"), "boq_line_source", ["bid_id"], unique=False)
    op.create_table(
        "client_boq",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("sheet_name", sa.String(length=120), nullable=True),
        sa.Column("header_row", sa.Integer(), nullable=True),
        sa.Column("column_map", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_client_boq_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f("fk_client_boq_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["document.id"],
            name=op.f("fk_client_boq_document_id_document"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_client_boq")),
    )
    op.create_index(op.f("ix_client_boq_bid_id"), "client_boq", ["bid_id"], unique=False)
    op.create_index(op.f("ix_client_boq_document_id"), "client_boq", ["document_id"], unique=False)
    op.create_table(
        "sheet",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("index_in_document", sa.Integer(), nullable=False),
        sa.Column("layout_name", sa.String(length=120), nullable=True),
        sa.Column("width_mm", sa.Float(), nullable=True),
        sa.Column("height_mm", sa.Float(), nullable=True),
        sa.Column("content_class", sa.String(length=16), nullable=True),
        sa.Column("quality_band", sa.String(length=16), nullable=True),
        sa.Column("manual_takeoff_recommended", sa.Boolean(), nullable=False),
        sa.Column("quality_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_sheet_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["document.id"],
            name=op.f("fk_sheet_document_id_document"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sheet")),
        sa.UniqueConstraint("document_id", "index_in_document", name="uq_sheet_index"),
    )
    op.create_index(op.f("ix_sheet_bid_id"), "sheet", ["bid_id"], unique=False)
    op.create_index(op.f("ix_sheet_document_id"), "sheet", ["document_id"], unique=False)
    op.create_table(
        "client_boq_line",
        sa.Column("client_boq_id", sa.Uuid(), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(length=200), nullable=True),
        sa.Column("item_no", sa.String(length=40), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(length=16), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=16, scale=3), nullable=True),
        sa.Column("unit_rate", firebid.db.types.MoneyType(precision=14, scale=2), nullable=True),
        sa.Column("amount_is_formula", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_client_boq_line_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["client_boq_id"],
            ["client_boq.id"],
            name=op.f("fk_client_boq_line_client_boq_id_client_boq"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_client_boq_line")),
    )
    op.create_index(op.f("ix_client_boq_line_bid_id"), "client_boq_line", ["bid_id"], unique=False)
    op.create_index(
        op.f("ix_client_boq_line_client_boq_id"), "client_boq_line", ["client_boq_id"], unique=False
    )
    op.create_table(
        "sheet_revision",
        sa.Column("sheet_id", sa.Uuid(), nullable=False),
        sa.Column("sheet_number", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("revision_label", sa.String(length=40), nullable=False),
        sa.Column("revision_date", sa.Date(), nullable=True),
        sa.Column("discipline", sa.String(length=40), nullable=True),
        sa.Column("level", sa.String(length=40), nullable=True),
        sa.Column("zone", sa.String(length=40), nullable=True),
        sa.Column("scale_text", sa.String(length=40), nullable=True),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
        sa.Column("addendum_id", sa.Uuid(), nullable=True),
        sa.Column("source_confidence", sa.Float(), nullable=True),
        sa.Column("extraction_method", sa.String(length=24), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "state IN ('received', 'registered', 'current', 'superseded', 'withdrawn', 'conflict')",
            name=op.f("ck_sheet_revision_state_known"),
        ),
        sa.ForeignKeyConstraint(
            ["addendum_id"],
            ["addendum.id"],
            name=op.f("fk_sheet_revision_addendum_id_addendum"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bid.id"], name=op.f("fk_sheet_revision_bid_id_bid"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f("fk_sheet_revision_created_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["sheet_id"],
            ["sheet.id"],
            name=op.f("fk_sheet_revision_sheet_id_sheet"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["sheet_revision.id"],
            name=op.f("fk_sheet_revision_superseded_by_id_sheet_revision"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sheet_revision")),
        sa.UniqueConstraint("bid_id", "sheet_number", "revision_label", name="uq_sheet_revision"),
    )
    op.create_index(
        op.f("ix_sheet_revision_addendum_id"), "sheet_revision", ["addendum_id"], unique=False
    )
    op.create_index(op.f("ix_sheet_revision_bid_id"), "sheet_revision", ["bid_id"], unique=False)
    op.create_index(
        op.f("ix_sheet_revision_sheet_id"), "sheet_revision", ["sheet_id"], unique=False
    )
    op.create_index(
        op.f("ix_sheet_revision_sheet_number"), "sheet_revision", ["sheet_number"], unique=False
    )
    op.create_index(op.f("ix_sheet_revision_state"), "sheet_revision", ["state"], unique=False)
    op.create_table(
        "client_boq_mapping",
        sa.Column("client_boq_line_id", sa.Uuid(), nullable=False),
        sa.Column("boq_line_id", sa.Uuid(), nullable=True),
        sa.Column("measured_quantity", sa.Numeric(precision=16, scale=3), nullable=True),
        sa.Column("variance_percent", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("confirmed_by_id", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"],
            ["bid.id"],
            name=op.f("fk_client_boq_mapping_bid_id_bid"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["boq_line_id"],
            ["boq_line.id"],
            name=op.f("fk_client_boq_mapping_boq_line_id_boq_line"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["client_boq_line_id"],
            ["client_boq_line.id"],
            name=op.f("fk_client_boq_mapping_client_boq_line_id_client_boq_line"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_id"],
            ["app_user.id"],
            name=op.f("fk_client_boq_mapping_confirmed_by_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_client_boq_mapping")),
        sa.UniqueConstraint("client_boq_line_id", name="uq_mapping_client_line"),
    )
    op.create_index(
        op.f("ix_client_boq_mapping_bid_id"), "client_boq_mapping", ["bid_id"], unique=False
    )
    op.create_index(
        op.f("ix_client_boq_mapping_boq_line_id"),
        "client_boq_mapping",
        ["boq_line_id"],
        unique=False,
    )
    # ### end Alembic commands ###
    _create_partitions()
    _make_append_only()
    _create_app_role()


def downgrade() -> None:
    _drop_app_role()
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS firebid_reject_write()")
    op.execute("DROP FUNCTION IF EXISTS ensure_audit_event_partition(date)")
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f("ix_client_boq_mapping_boq_line_id"), table_name="client_boq_mapping")
    op.drop_index(op.f("ix_client_boq_mapping_bid_id"), table_name="client_boq_mapping")
    op.drop_table("client_boq_mapping")
    op.drop_index(op.f("ix_sheet_revision_state"), table_name="sheet_revision")
    op.drop_index(op.f("ix_sheet_revision_sheet_number"), table_name="sheet_revision")
    op.drop_index(op.f("ix_sheet_revision_sheet_id"), table_name="sheet_revision")
    op.drop_index(op.f("ix_sheet_revision_bid_id"), table_name="sheet_revision")
    op.drop_index(op.f("ix_sheet_revision_addendum_id"), table_name="sheet_revision")
    op.drop_table("sheet_revision")
    op.drop_index(op.f("ix_client_boq_line_client_boq_id"), table_name="client_boq_line")
    op.drop_index(op.f("ix_client_boq_line_bid_id"), table_name="client_boq_line")
    op.drop_table("client_boq_line")
    op.drop_index(op.f("ix_sheet_document_id"), table_name="sheet")
    op.drop_index(op.f("ix_sheet_bid_id"), table_name="sheet")
    op.drop_table("sheet")
    op.drop_index(op.f("ix_client_boq_document_id"), table_name="client_boq")
    op.drop_index(op.f("ix_client_boq_bid_id"), table_name="client_boq")
    op.drop_table("client_boq")
    op.drop_index(op.f("ix_boq_line_source_bid_id"), table_name="boq_line_source")
    op.drop_table("boq_line_source")
    op.drop_index(op.f("ix_evidence_qto_item_id"), table_name="evidence")
    op.drop_table("evidence")
    op.drop_index(op.f("ix_document_tender_package_id"), table_name="document")
    op.drop_index(op.f("ix_document_state"), table_name="document")
    op.drop_index(op.f("ix_document_kind"), table_name="document")
    op.drop_index(op.f("ix_document_bid_id"), table_name="document")
    op.drop_table("document")
    op.drop_index(op.f("ix_boq_line_rate_id"), table_name="boq_line")
    op.drop_index(op.f("ix_boq_line_boq_id"), table_name="boq_line")
    op.drop_index(op.f("ix_boq_line_bid_id"), table_name="boq_line")
    op.drop_table("boq_line")
    op.drop_index(op.f("ix_addendum_bid_id"), table_name="addendum")
    op.drop_table("addendum")
    op.drop_index(op.f("ix_tender_package_bid_id"), table_name="tender_package")
    op.drop_table("tender_package")
    op.drop_index(op.f("ix_qto_item_state"), table_name="qto_item")
    op.drop_index(op.f("ix_qto_item_level"), table_name="qto_item")
    op.drop_index(op.f("ix_qto_item_item_type"), table_name="qto_item")
    op.drop_index(op.f("ix_qto_item_duplicate_group_id"), table_name="qto_item")
    op.drop_table("qto_item")
    op.drop_index(op.f("ix_human_task_state"), table_name="human_task")
    op.drop_index(op.f("ix_human_task_kind"), table_name="human_task")
    op.drop_index(op.f("ix_human_task_bid_id"), table_name="human_task")
    op.drop_index(op.f("ix_human_task_assignee_id"), table_name="human_task")
    op.drop_table("human_task")
    op.drop_index(op.f("ix_detected_object_sheet_revision_id"), table_name="detected_object")
    op.drop_index(op.f("ix_detected_object_sheet_id"), table_name="detected_object")
    op.drop_index(op.f("ix_detected_object_object_type"), table_name="detected_object")
    op.drop_table("detected_object")
    op.drop_index(op.f("ix_boq_bid_id"), table_name="boq")
    op.drop_table("boq")
    op.drop_index(op.f("ix_bid_member_user_id"), table_name="bid_member")
    op.drop_table("bid_member")
    op.drop_index(op.f("ix_approval_gate"), table_name="approval")
    op.drop_index(op.f("ix_approval_bid_id"), table_name="approval")
    op.drop_table("approval")
    op.drop_index(op.f("ix_agent_run_route"), table_name="agent_run")
    op.drop_index(op.f("ix_agent_run_input_hash"), table_name="agent_run")
    op.drop_index(op.f("ix_agent_run_bid_id"), table_name="agent_run")
    op.drop_table("agent_run")
    op.drop_index(op.f("ix_bid_state"), table_name="bid")
    op.drop_index(op.f("ix_bid_project_id"), table_name="bid")
    op.drop_index(op.f("ix_bid_organisation_id"), table_name="bid")
    op.drop_table("bid")
    op.drop_table("user_role")
    op.drop_index(op.f("ix_rate_organisation_id"), table_name="rate")
    op.drop_index(op.f("ix_rate_item_key"), table_name="rate")
    op.drop_table("rate")
    op.drop_index(op.f("ix_project_organisation_id"), table_name="project")
    op.drop_table("project")
    op.drop_index(op.f("ix_measurement_rule_organisation_id"), table_name="measurement_rule")
    op.drop_table("measurement_rule")
    op.drop_table("audit_retention")
    op.drop_index(op.f("ix_app_user_organisation_id"), table_name="app_user")
    op.drop_table("app_user")
    op.drop_table("organisation")
    op.drop_table("id_counter")
    op.drop_index("ix_audit_event_entity", table_name="audit_event")
    op.drop_index("ix_audit_event_bid_occurred", table_name="audit_event")
    op.drop_index("ix_audit_event_actor", table_name="audit_event")
    op.drop_table("audit_event")
    op.drop_index(op.f("ix_audit_chain_link_chain_key"), table_name="audit_chain_link")
    op.drop_table("audit_chain_link")
    # ### end Alembic commands ###
