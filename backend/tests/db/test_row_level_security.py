"""Row-level security: the database refuses another bid's rows even without the app's filter.

These tests deliberately bypass the application layer and run raw SQL as the application's own
database role, which is what an injection or a missed filter would do.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from firebid.db.engine import sqlalchemy_url
from firebid.db.models.core import AppUser, Bid, BidMember, Organisation
from tests.db.conftest import APP_ROLE_PASSWORD
from tests.db.factories import make_qto_item

pytestmark = pytest.mark.req("NFR-08")


@pytest.fixture
def service_role_engine(database_url: str) -> Iterator[Engine]:
    url = sqlalchemy_url(database_url).replace(
        "firebid:firebid@", "firebid_service:firebid-service@"
    )
    engine = create_engine(url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture
def two_bids(session: Session, organisation: Organisation, bid: Bid) -> tuple[Bid, Bid, AppUser]:
    """Two bids; the person belongs to the first one only."""
    other = Bid(
        organisation_id=organisation.id,
        project_id=bid.project_id,
        human_id="BID-2026-099",
        client_name="Rival Main Contractor",
        tender_reference="MC/2026/FP/099",
        submission_deadline=bid.submission_deadline,
    )
    person = AppUser(
        organisation_id=organisation.id,
        external_id=uuid.uuid4().hex,
        username="member@firebid.test",
        display_name="Member",
    )
    session.add_all([other, person])
    session.flush()
    session.add(BidMember(bid_id=bid.id, user_id=person.id, role="estimator"))
    make_qto_item(session, bid, human_id="QTO-000100")
    make_qto_item(session, other, human_id="QTO-000200")
    session.commit()
    return bid, other, person


def rows_visible(
    engine: Engine,
    user_id: uuid.UUID | None,
    statement: str,
    params: dict[str, object] | None = None,
) -> int:
    with engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": str(user_id) if user_id else ""},
        )
        return int(connection.execute(text(statement), params or {}).scalar_one())


class TestApplicationRole:
    def test_sees_only_its_own_bid(
        self, app_role_engine: Engine, two_bids: tuple[Bid, Bid, AppUser]
    ) -> None:
        _member_bid, other_bid, person = two_bids
        assert rows_visible(app_role_engine, person.id, "SELECT count(*) FROM bid") == 1
        assert (
            rows_visible(
                app_role_engine,
                person.id,
                "SELECT count(*) FROM bid WHERE id = :bid_id",
                {"bid_id": other_bid.id},
            )
            == 0
        )

    def test_sees_only_its_own_bid_data(
        self, app_role_engine: Engine, two_bids: tuple[Bid, Bid, AppUser]
    ) -> None:
        _, other_bid, person = two_bids
        assert rows_visible(app_role_engine, person.id, "SELECT count(*) FROM qto_item") == 1
        assert (
            rows_visible(
                app_role_engine,
                person.id,
                "SELECT count(*) FROM qto_item WHERE bid_id = :bid_id",
                {"bid_id": other_bid.id},
            )
            == 0
        )

    def test_sees_nothing_when_no_user_is_set(
        self, app_role_engine: Engine, two_bids: tuple[Bid, Bid, AppUser]
    ) -> None:
        assert rows_visible(app_role_engine, None, "SELECT count(*) FROM bid") == 0
        assert rows_visible(app_role_engine, None, "SELECT count(*) FROM qto_item") == 0
        assert rows_visible(app_role_engine, None, "SELECT count(*) FROM audit_event") == 0

    def test_cannot_write_into_another_bid(
        self, app_role_engine: Engine, two_bids: tuple[Bid, Bid, AppUser]
    ) -> None:
        _, other_bid, person = two_bids
        with (
            app_role_engine.begin() as connection,
            pytest.raises(Exception, match="row-level security"),
        ):
            connection.execute(
                text("SELECT set_config('app.user_id', :user_id, true)"),
                {"user_id": str(person.id)},
            )
            connection.execute(
                text(
                    "INSERT INTO qto_item (id, bid_id, human_id, item_type, description, unit,"
                    " net_quantity, calculation_method, state, version, is_manual, attributes)"
                    " VALUES (gen_random_uuid(), :bid_id, 'QTO-000999', 'pipe', 'sneaked in', 'm',"
                    " 1, 'count', 'detected', 1, false, '{}'::jsonb)"
                ),
                {"bid_id": str(other_bid.id)},
            )


class TestServiceRole:
    def test_crosses_bids_for_jobs_that_must(
        self, service_role_engine: Engine, two_bids: tuple[Bid, Bid, AppUser]
    ) -> None:
        assert rows_visible(service_role_engine, None, "SELECT count(*) FROM bid") == 2

    def test_still_cannot_rewrite_history(
        self, service_role_engine: Engine, two_bids: tuple[Bid, Bid, AppUser]
    ) -> None:
        with (
            service_role_engine.begin() as connection,
            pytest.raises(Exception, match="permission denied"),
        ):
            connection.execute(text("UPDATE audit_event SET reason = 'changed'"))


class TestEveryBidTableIsProtected:
    def test_no_table_with_a_bid_id_is_left_without_a_policy(self, engine: Engine) -> None:
        with engine.begin() as connection:
            unprotected = (
                connection.execute(
                    text(
                        """
                    SELECT c.relname FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    JOIN pg_attribute a ON a.attrelid = c.oid
                         AND a.attname = 'bid_id' AND a.attnum > 0
                    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
                      AND (NOT c.relrowsecurity
                           OR NOT EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid))
                    ORDER BY c.relname
                    """
                    )
                )
                .scalars()
                .all()
            )
        assert list(unprotected) == [], (
            "these tables hold bid data without row-level security; add policies in a migration"
        )

    def test_neither_role_can_bypass(self, engine: Engine) -> None:
        with engine.begin() as connection:
            bypassers = (
                connection.execute(
                    text(
                        "SELECT rolname FROM pg_roles"
                        " WHERE rolname IN ('firebid_app', 'firebid_service') AND rolbypassrls"
                    )
                )
                .scalars()
                .all()
            )
        assert list(bypassers) == []


def test_the_app_role_password_is_configurable() -> None:
    """The migration reads FIREBID_APP_DB_PASSWORD; the test database proves it took effect."""
    assert APP_ROLE_PASSWORD != "firebid-app"  # noqa: S105  # the default, not a secret


@pytest.mark.req("NFR-08")
def test_a_partition_created_later_is_protected_too(engine: Engine) -> None:
    """The nightly job makes partitions; migration 0006 makes them arrive with policies."""
    with engine.begin() as connection:
        name = connection.execute(
            text("SELECT ensure_audit_event_partition(:target)"), {"target": "2031-05-01"}
        ).scalar_one()
        protected = connection.execute(
            text(
                """
                SELECT c.relrowsecurity,
                       (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid)
                FROM pg_class c WHERE c.relname = :name
                """
            ),
            {"name": name},
        ).one()
    assert protected[0] is True, f"{name} was created without row-level security"
    assert protected[1] == 2, f"{name} is missing the application or service policy"
