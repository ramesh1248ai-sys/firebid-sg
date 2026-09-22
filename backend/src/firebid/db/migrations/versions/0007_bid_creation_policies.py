"""Let a bid be created under row-level security, without widening what anyone can see.

Migration 0003 keyed every policy on membership. That is right for reading a bid but wrong for
creating one: at the moment of the insert the creator is not yet a member, so the row they are
writing fails its own check, and staffing the new bid fails for the same reason.

The fix splits the commands apart. Creating is allowed when the row names the acting user as
its creator; changing and deleting still require membership. Reading admits the creator as well
as the team, because an ``INSERT ... RETURNING`` reads the new row back before the creator has
been added to it. Staffing is allowed to members and to the bid's creator, so the first member
can be added and no further.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# SECURITY DEFINER, like firebid_can_see_bid, so the policy on bid_member can read bid without
# tripping over bid's own policy.
CREATED_BID = """
CREATE OR REPLACE FUNCTION firebid_created_bid(target uuid)
RETURNS boolean AS $$
    SELECT EXISTS (
        SELECT 1 FROM bid
        WHERE bid.id = target AND bid.created_by_id = firebid_current_user_id()
    )
$$ LANGUAGE sql STABLE SECURITY DEFINER;
"""

SPLIT_POLICIES = """
DO $$
BEGIN
    DROP POLICY IF EXISTS firebid_app_bid_scope ON bid;
    -- The creator is on the list as well as the team: an INSERT ... RETURNING has to read the
    -- row back, and at that instant the creator is not yet a member of their own new bid.
    EXECUTE 'CREATE POLICY firebid_app_bid_select ON bid FOR SELECT TO firebid_app '
            'USING (firebid_can_see_bid(id) OR created_by_id = firebid_current_user_id())';
    -- A bid may be created only in the acting user''s own name.
    EXECUTE 'CREATE POLICY firebid_app_bid_insert ON bid FOR INSERT TO firebid_app '
            'WITH CHECK (created_by_id = firebid_current_user_id())';
    EXECUTE 'CREATE POLICY firebid_app_bid_update ON bid FOR UPDATE TO firebid_app '
            'USING (firebid_can_see_bid(id)) WITH CHECK (firebid_can_see_bid(id))';
    EXECUTE 'CREATE POLICY firebid_app_bid_delete ON bid FOR DELETE TO firebid_app '
            'USING (firebid_can_see_bid(id))';

    DROP POLICY IF EXISTS firebid_app_bid_scope ON bid_member;
    EXECUTE 'CREATE POLICY firebid_app_bid_scope ON bid_member FOR ALL TO firebid_app '
            'USING (firebid_can_see_bid(bid_id) OR user_id = firebid_current_user_id()) '
            'WITH CHECK (firebid_can_see_bid(bid_id) OR firebid_created_bid(bid_id))';
END $$;
"""

RESTORE_POLICIES = """
DO $$
BEGIN
    DROP POLICY IF EXISTS firebid_app_bid_select ON bid;
    DROP POLICY IF EXISTS firebid_app_bid_insert ON bid;
    DROP POLICY IF EXISTS firebid_app_bid_update ON bid;
    DROP POLICY IF EXISTS firebid_app_bid_delete ON bid;
    EXECUTE 'CREATE POLICY firebid_app_bid_scope ON bid FOR ALL TO firebid_app '
            'USING (firebid_can_see_bid(id)) WITH CHECK (firebid_can_see_bid(id))';

    DROP POLICY IF EXISTS firebid_app_bid_scope ON bid_member;
    EXECUTE 'CREATE POLICY firebid_app_bid_scope ON bid_member FOR ALL TO firebid_app '
            'USING (firebid_can_see_bid(bid_id) OR user_id = firebid_current_user_id()) '
            'WITH CHECK (firebid_can_see_bid(bid_id))';
END $$;
"""


def upgrade() -> None:
    op.execute(CREATED_BID)
    op.execute(SPLIT_POLICIES)


def downgrade() -> None:
    op.execute(RESTORE_POLICIES)
    op.execute("DROP FUNCTION IF EXISTS firebid_created_bid(uuid)")
