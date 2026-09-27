"""Let the people who edit organisation libraries record the audit event for it (FR-ADM-02).

Until now the application role could insert an audit event only for a bid it can see: a
bid-less, organisation-level event was refused for everyone (the partitions allowed it for a
system administrator, but inserts go through the parent table, whose check did not). So an
edit to the canonical object library, which belongs to no bid, could not be audited, and so
could not be saved.

Now a bid-less event may be **written** by a system administrator or a senior estimator: the
roles that may change the object library (`object_library.change`). **Reading**
organisation-level events stays with system administrators, as before. Bid events are
unchanged: only members of the bid write or read them.

The same writers may read and write the organisation chain's `audit_chain_link` rows: a new
link is hashed onto the previous one, so a writer that could not read the previous link
would restart the sequence and break the chain. Links hold only hashes and counts.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

READ = "firebid_can_see_bid(bid_id) OR (bid_id IS NULL AND firebid_has_role('system_admin'))"
WRITE = (
    "firebid_can_see_bid(bid_id) OR (bid_id IS NULL AND (firebid_has_role('system_admin') "
    "OR firebid_has_role('senior_estimator')))"
)
OLD_WRITE = "firebid_can_see_bid(bid_id)"
PARTITION_OLD = (
    "CASE WHEN bid_id IS NULL THEN firebid_has_role('system_admin') "
    "ELSE firebid_can_see_bid(bid_id) END"
)


def _function(check: str) -> str:
    quoted = check.replace("'", "''")
    read = PARTITION_OLD.replace("'", "''")
    return f"""
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
            'USING ({read}) WITH CHECK ({quoted})', part_name);
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
"""  # noqa: S608 - fixed policy text defined in this module


def _partitions(check: str) -> str:
    quoted = check.replace("'", "''")
    return f"""
DO $$
DECLARE part record;
BEGIN
    FOR part IN
        SELECT c.relname FROM pg_class c
        JOIN pg_inherits i ON i.inhrelid = c.oid
        JOIN pg_class parent ON parent.oid = i.inhparent
        WHERE parent.relname = 'audit_event'
    LOOP
        EXECUTE format('ALTER POLICY firebid_app_bid_scope ON %I WITH CHECK ({quoted})',
                       part.relname);
    END LOOP;
END $$;
"""  # noqa: S608 - fixed policy text defined in this module


def upgrade() -> None:
    op.execute(
        f"ALTER POLICY firebid_app_bid_scope ON audit_event USING ({READ}) WITH CHECK ({WRITE})"
    )
    op.execute(
        f"ALTER POLICY firebid_app_bid_scope ON audit_chain_link USING ({WRITE}) WITH CHECK ({WRITE})"
    )
    op.execute(_partitions(WRITE))
    op.execute(_function(WRITE))


def downgrade() -> None:
    op.execute(
        f"ALTER POLICY firebid_app_bid_scope ON audit_chain_link USING ({OLD_WRITE}) "
        f"WITH CHECK ({OLD_WRITE})"
    )
    op.execute(
        f"ALTER POLICY firebid_app_bid_scope ON audit_event USING ({READ}) WITH CHECK ({OLD_WRITE})"
    )
    op.execute(_partitions(PARTITION_OLD))
    op.execute(_function(PARTITION_OLD))
