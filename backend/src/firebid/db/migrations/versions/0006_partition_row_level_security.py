"""Row-level security on audit partitions created after the fact.

Migration 0003 protected the partitions that existed then. A partition made later, by the
nightly job, inherited the parent's triggers but not its policies, so it would have sat in the
schema unprotected. The creating function now applies them itself.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Policies match audit_event's in migration 0003: bid members see their bid; a bid-less
# (organisation-wide) event is for system administrators only.
WITH_POLICIES = """
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
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', part_name);
    IF NOT EXISTS (
        SELECT 1 FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
        WHERE c.relname = part_name AND p.polname = 'firebid_app_bid_scope'
    ) THEN
        EXECUTE format(
            'CREATE POLICY firebid_app_bid_scope ON %I FOR ALL TO firebid_app '
            'USING (CASE WHEN bid_id IS NULL THEN firebid_has_role(''system_admin'') '
            'ELSE firebid_can_see_bid(bid_id) END) '
            'WITH CHECK (CASE WHEN bid_id IS NULL THEN firebid_has_role(''system_admin'') '
            'ELSE firebid_can_see_bid(bid_id) END)', part_name);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
        WHERE c.relname = part_name AND p.polname = 'firebid_service_all'
    ) THEN
        EXECUTE format(
            'CREATE POLICY firebid_service_all ON %I FOR ALL TO firebid_service '
            'USING (true) WITH CHECK (true)', part_name);
    END IF;
    RETURN part_name;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
"""

WITHOUT_POLICIES = """
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


def upgrade() -> None:
    op.execute(WITH_POLICIES)
    # Bring partitions that were created between 0003 and now under the same policies.
    op.execute(
        """
        DO $$
        DECLARE part record;
        BEGIN
            FOR part IN
                SELECT c.relname FROM pg_class c
                JOIN pg_inherits i ON i.inhrelid = c.oid
                JOIN pg_class parent ON parent.oid = i.inhparent
                WHERE parent.relname = 'audit_event'
            LOOP
                PERFORM ensure_audit_event_partition(
                    to_date(right(part.relname, 6), 'YYYYMM'));
            END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(WITHOUT_POLICIES)
