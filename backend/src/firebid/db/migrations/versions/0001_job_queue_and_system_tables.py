"""Job queue schema (Procrastinate) and system tables.

Alembic owns every schema change, including the job queue's. When Procrastinate is upgraded,
add a revision that applies the SQL files it ships in ``procrastinate/sql/migrations`` (ADR-006).

Revision ID: 0001
Revises:
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from procrastinate.schema import SchemaManager
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Drops every object the Procrastinate schema created (all named procrastinate_*).
_DROP_PROCRASTINATE = """
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT tablename FROM pg_tables
             WHERE schemaname = current_schema() AND tablename LIKE 'procrastinate%' LOOP
        EXECUTE format('DROP TABLE IF EXISTS %I CASCADE', r.tablename);
    END LOOP;
    FOR r IN SELECT p.oid::regprocedure AS fn FROM pg_proc p
             JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = current_schema() AND p.proname LIKE 'procrastinate%' LOOP
        EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', r.fn);
    END LOOP;
    FOR r IN SELECT t.typname FROM pg_type t
             JOIN pg_namespace n ON n.oid = t.typnamespace
             WHERE n.nspname = current_schema() AND t.typname LIKE 'procrastinate%'
               AND t.typtype IN ('e', 'c') LOOP
        EXECUTE format('DROP TYPE IF EXISTS %I CASCADE', r.typname);
    END LOOP;
END $$;
"""


def _execute_verbatim(sql: str) -> None:
    """Run SQL as written. psycopg treats '%' as a placeholder, so escape it (as Procrastinate's
    own apply_schema does)."""
    op.get_bind().exec_driver_sql(sql.replace("%", "%%"))


def upgrade() -> None:
    _execute_verbatim(SchemaManager.get_schema())

    op.create_table(
        "system_heartbeat",
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_system_heartbeat")),
    )
    op.create_table(
        "system_job_result",
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("task_name", sa.String(128), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("job_id", name=op.f("pk_system_job_result")),
    )


def downgrade() -> None:
    op.drop_table("system_job_result")
    op.drop_table("system_heartbeat")
    _execute_verbatim(_DROP_PROCRASTINATE)
