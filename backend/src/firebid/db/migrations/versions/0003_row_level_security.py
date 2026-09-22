"""Row-level security on bid data, and the service role for cross-bid jobs.

The application filters by bid membership in code. This migration makes the database enforce
the same rule, so a missed filter, an injection or a stray query still cannot read another
bid's data (NFR-08, guardrail 8).

Policies call SECURITY DEFINER helpers, which read membership without triggering the policy on
`bid_member` itself (that would recurse). The table owner is not subject to these policies, so
migrations and administrative tasks are unaffected.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-22
"""

import os
from collections.abc import Sequence
from typing import Any

from alembic import op
from psycopg import sql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "firebid_app"
SERVICE_ROLE = "firebid_service"
APPEND_ONLY_TABLES = ("audit_event", "audit_chain_link")

HELPERS = """
-- The acting user for this transaction; empty when none was set.
CREATE OR REPLACE FUNCTION firebid_current_user_id() RETURNS uuid AS $$
    SELECT nullif(current_setting('app.user_id', true), '')::uuid
$$ LANGUAGE sql STABLE;

-- SECURITY DEFINER: reads membership as the owner, so the policy on bid_member does not recurse.
CREATE OR REPLACE FUNCTION firebid_can_see_bid(target uuid) RETURNS boolean AS $$
    SELECT target IS NOT NULL AND EXISTS (
        SELECT 1 FROM bid_member m
        WHERE m.bid_id = target AND m.user_id = firebid_current_user_id()
    )
$$ LANGUAGE sql STABLE SECURITY DEFINER;

CREATE OR REPLACE FUNCTION firebid_has_role(wanted text) RETURNS boolean AS $$
    SELECT EXISTS (
        SELECT 1 FROM user_role r
        WHERE r.user_id = firebid_current_user_id() AND r.role = wanted
    )
$$ LANGUAGE sql STABLE SECURITY DEFINER;
"""

# Every table with a bid_id column, including partitions, gets the same pair of policies.
APPLY_POLICIES = """
DO $$
DECLARE
    r record;
    using_clause text;
BEGIN
    FOR r IN
        SELECT c.relname AS table_name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'bid_id' AND a.attnum > 0
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
          AND c.relname <> 'bid_member'  -- gets its own, wider policy below
    LOOP
        -- Organisation-level audit rows carry no bid; only administrators may read them.
        IF r.table_name = 'audit_event' THEN
            using_clause := 'firebid_can_see_bid(bid_id) OR (bid_id IS NULL AND '
                            || 'firebid_has_role(''system_admin''))';
        ELSE
            using_clause := 'firebid_can_see_bid(bid_id)';
        END IF;

        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', r.table_name);
        EXECUTE format(
            'CREATE POLICY firebid_app_bid_scope ON %I FOR ALL TO %I USING (%s) WITH CHECK (%s)',
            r.table_name, 'firebid_app', using_clause, 'firebid_can_see_bid(bid_id)');
        EXECUTE format(
            'CREATE POLICY firebid_service_all ON %I FOR ALL TO %I USING (true) WITH CHECK (true)',
            r.table_name, 'firebid_service');
    END LOOP;

    -- The bid itself is keyed by id, and membership is visible to the member.
    EXECUTE 'ALTER TABLE bid ENABLE ROW LEVEL SECURITY';
    EXECUTE 'CREATE POLICY firebid_app_bid_scope ON bid FOR ALL TO firebid_app '
            'USING (firebid_can_see_bid(id)) WITH CHECK (firebid_can_see_bid(id))';
    EXECUTE 'CREATE POLICY firebid_service_all ON bid FOR ALL TO firebid_service '
            'USING (true) WITH CHECK (true)';

    EXECUTE 'ALTER TABLE bid_member ENABLE ROW LEVEL SECURITY';
    EXECUTE 'CREATE POLICY firebid_app_bid_scope ON bid_member FOR ALL TO firebid_app '
            'USING (firebid_can_see_bid(bid_id) OR user_id = firebid_current_user_id()) '
            'WITH CHECK (firebid_can_see_bid(bid_id))';
    EXECUTE 'CREATE POLICY firebid_service_all ON bid_member FOR ALL TO firebid_service '
            'USING (true) WITH CHECK (true)';
END $$;
"""

REMOVE_POLICIES = """
DO $$
DECLARE r record;
BEGIN
    FOR r IN
        SELECT c.relname AS table_name FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
          AND EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid)
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS firebid_app_bid_scope ON %I', r.table_name);
        EXECUTE format('DROP POLICY IF EXISTS firebid_service_all ON %I', r.table_name);
        EXECUTE format('ALTER TABLE %I DISABLE ROW LEVEL SECURITY', r.table_name);
    END LOOP;
END $$;
DROP FUNCTION IF EXISTS firebid_can_see_bid(uuid);
DROP FUNCTION IF EXISTS firebid_has_role(text);
DROP FUNCTION IF EXISTS firebid_current_user_id();
"""


def _driver_connection() -> Any:
    connection = op.get_bind().connection.driver_connection
    if connection is None:
        raise RuntimeError("no database connection")
    return connection


def _service_role_password() -> str:
    return os.environ.get("FIREBID_SERVICE_DB_PASSWORD", "firebid-service")


def _create_service_role() -> None:
    """Used only by jobs that span bids. Like the app role, it cannot rewrite history."""
    connection = _driver_connection()
    role = sql.Identifier(SERVICE_ROLE)
    connection.execute(
        sql.SQL(
            "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {name}) "
            "THEN CREATE ROLE {role} LOGIN; END IF; END $$;"
        ).format(name=sql.Literal(SERVICE_ROLE), role=role)
    )
    connection.execute(
        sql.SQL("ALTER ROLE {role} WITH LOGIN PASSWORD {password} NOBYPASSRLS").format(
            role=role, password=sql.Literal(_service_role_password())
        )
    )
    for statement in [
        "GRANT USAGE ON SCHEMA public TO {role}",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}",
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}",
        "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {role}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {role}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO {role}",
        f"REVOKE UPDATE, DELETE ON {', '.join(APPEND_ONLY_TABLES)} FROM {{role}}",
    ]:
        connection.execute(sql.SQL(statement).format(role=role))


def _drop_service_role() -> None:
    connection = _driver_connection()
    connection.execute(
        sql.SQL(
            "DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname = {name}) THEN "
            "EXECUTE 'DROP OWNED BY {role_literal}'; END IF; END $$;"
        ).format(name=sql.Literal(SERVICE_ROLE), role_literal=sql.SQL(SERVICE_ROLE))
    )
    connection.execute(
        sql.SQL("DROP ROLE IF EXISTS {role}").format(role=sql.Identifier(SERVICE_ROLE))
    )


def upgrade() -> None:
    _create_service_role()
    op.execute(HELPERS)
    op.execute(APPLY_POLICIES)
    # The helpers are the only way the app role reads membership for a policy.
    op.execute(f"GRANT EXECUTE ON FUNCTION firebid_can_see_bid(uuid) TO {APP_ROLE}, {SERVICE_ROLE}")
    op.execute(f"GRANT EXECUTE ON FUNCTION firebid_has_role(text) TO {APP_ROLE}, {SERVICE_ROLE}")
    op.execute(f"GRANT EXECUTE ON FUNCTION firebid_current_user_id() TO {APP_ROLE}, {SERVICE_ROLE}")


def downgrade() -> None:
    op.execute(REMOVE_POLICIES)
    _drop_service_role()
