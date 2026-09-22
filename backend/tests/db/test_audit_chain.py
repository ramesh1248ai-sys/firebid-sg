"""The audit trail: append-only in the database, tamper-evident, and never a bottleneck."""

from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from firebid.db.audit import record_event, verify_chain
from firebid.db.models.audit import AuditChainLink, AuditEvent
from firebid.db.models.core import Bid
from firebid.domain.actors import Actor, AuditContext
from firebid.domain.state_machines import QtoItemState
from firebid.services.transitions import apply_transition
from tests.db.factories import make_qto_item

pytestmark = pytest.mark.req("NFR-09")


def write_event(session: Session, bid: Bid, actor: Actor, action: str = "noted") -> AuditEvent:
    return record_event(
        session,
        context=AuditContext(organisation_id=bid.organisation_id, bid_id=bid.id),
        actor=actor,
        action=action,
        entity_type="bid",
        entity_id=bid.id,
        after={"note": action},
    )


class TestAppendOnly:
    def test_the_application_role_cannot_change_or_delete_history(
        self, session: Session, bid: Bid, estimator: Actor, app_role_engine: Engine
    ) -> None:
        write_event(session, bid, estimator)
        session.commit()

        with app_role_engine.begin() as connection:
            rows = connection.execute(text("SELECT count(*) FROM audit_event")).scalar_one()
            assert rows == 1  # the application can read
            with pytest.raises(ProgrammingError, match="permission denied"):
                connection.execute(text("UPDATE audit_event SET reason = 'changed'"))
        with (
            app_role_engine.begin() as connection,
            pytest.raises(ProgrammingError, match="permission denied"),
        ):
            connection.execute(text("DELETE FROM audit_event"))

    def test_even_the_owner_is_refused_by_the_trigger(
        self, session: Session, bid: Bid, estimator: Actor
    ) -> None:
        write_event(session, bid, estimator)
        session.commit()

        for statement in ("UPDATE audit_event SET reason = 'changed'", "DELETE FROM audit_event"):
            with pytest.raises(DBAPIError, match="append-only"):
                session.execute(text(statement))
            session.rollback()


class TestTamperEvidence:
    def test_an_intact_chain_verifies(self, session: Session, bid: Bid, estimator: Actor) -> None:
        for index in range(3):
            write_event(session, bid, estimator, action=f"step {index}")
            session.commit()

        assert verify_chain(session, bid.id) == []
        links = (
            session.execute(
                select(AuditChainLink)
                .where(AuditChainLink.chain_key == bid.id)
                .order_by(AuditChainLink.seq)
            )
            .scalars()
            .all()
        )
        assert [link.seq for link in links] == [1, 2, 3]
        assert links[0].prev_hash is None
        assert links[1].prev_hash == links[0].hash

    def test_a_row_altered_with_owner_rights_is_detected(
        self, session: Session, bid: Bid, estimator: Actor
    ) -> None:
        write_event(session, bid, estimator, action="original")
        session.commit()

        # Only someone who can disable the trigger can do this; the point is that it shows.
        session.execute(text("ALTER TABLE audit_event DISABLE TRIGGER audit_event_append_only"))
        session.execute(text("UPDATE audit_event SET reason = 'quietly changed'"))
        session.execute(text("ALTER TABLE audit_event ENABLE TRIGGER audit_event_append_only"))
        session.commit()

        problems = verify_chain(session, bid.id)
        assert [problem.detail for problem in problems] == [
            "an event was altered after it was recorded"
        ]

    def test_a_deleted_event_is_detected(
        self, session: Session, bid: Bid, estimator: Actor
    ) -> None:
        write_event(session, bid, estimator)
        session.commit()
        session.execute(text("ALTER TABLE audit_event DISABLE TRIGGER audit_event_append_only"))
        session.execute(text("DELETE FROM audit_event"))
        session.execute(text("ALTER TABLE audit_event ENABLE TRIGGER audit_event_append_only"))
        session.commit()

        assert any(
            "expected 1 events" in problem.detail for problem in verify_chain(session, bid.id)
        )


class TestChainLinksPerTransaction:
    def test_a_bulk_transition_of_5000_items_appends_one_link(
        self, session: Session, bid: Bid, estimator: Actor
    ) -> None:
        items = [
            make_qto_item(session, bid, QtoItemState.PROPOSED, f"QTO-{index:06d}")
            for index in range(1, 5001)
        ]
        for item in items:
            apply_transition(session, item, target=QtoItemState.VERIFIED, actor=estimator)
        session.commit()

        links = (
            session.execute(select(AuditChainLink).where(AuditChainLink.chain_key == bid.id))
            .scalars()
            .all()
        )
        assert len(links) == 1
        assert links[0].event_count == 5000
        assert verify_chain(session, bid.id) == []

    def test_events_for_two_bids_in_one_transaction_get_a_link_each(
        self, session: Session, bid: Bid, estimator: Actor
    ) -> None:
        other = Bid(
            organisation_id=bid.organisation_id,
            project_id=bid.project_id,
            human_id="BID-2026-015",
            client_name="Another Main Contractor",
            tender_reference="MC/2026/FP/015",
            submission_deadline=bid.submission_deadline,
        )
        session.add(other)
        session.flush()

        write_event(session, bid, estimator)
        write_event(session, other, estimator)
        session.commit()

        assert session.execute(select(func.count()).select_from(AuditChainLink)).scalar_one() == 2
        assert verify_chain(session, bid.id) == []
        assert verify_chain(session, other.id) == []


class TestConcurrency:
    def test_two_bids_do_not_wait_on_each_other(
        self, engine: Engine, session: Session, bid: Bid, estimator: Actor
    ) -> None:
        """One bid holds its chain lock; another bid's write must not block."""
        other = Bid(
            organisation_id=bid.organisation_id,
            project_id=bid.project_id,
            human_id="BID-2026-016",
            client_name="Second Main Contractor",
            tender_reference="MC/2026/FP/016",
            submission_deadline=bid.submission_deadline,
        )
        session.add(other)
        session.commit()

        factory = sessionmaker(bind=engine, expire_on_commit=False)
        holding = threading.Event()
        release = threading.Event()

        def hold_first_chain() -> None:
            with factory() as holder:
                write_event(holder, bid, estimator, action="holding")
                from firebid.db.audit import append_chain_links

                append_chain_links(holder)  # takes this bid's advisory lock
                holding.set()
                release.wait(timeout=10)
                holder.commit()

        thread = threading.Thread(target=hold_first_chain)
        thread.start()
        try:
            assert holding.wait(timeout=10)
            started = time.monotonic()
            with factory() as second:
                write_event(second, other, estimator, action="not blocked")
                second.commit()
            elapsed = time.monotonic() - started
        finally:
            release.set()
            thread.join(timeout=15)

        assert elapsed < 2.0, f"writing to another bid waited {elapsed:.1f}s"
        assert verify_chain(session, other.id) == []

    def test_the_same_bid_is_serialised(
        self, engine: Engine, session: Session, bid: Bid, estimator: Actor
    ) -> None:
        """The other side of the same coin: one chain, one writer at a time."""
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        holding = threading.Event()
        release = threading.Event()
        blocked: list[bool] = []

        def hold_chain() -> None:
            with factory() as holder:
                write_event(holder, bid, estimator, action="holding")
                from firebid.db.audit import append_chain_links

                append_chain_links(holder)
                holding.set()
                release.wait(timeout=10)
                holder.commit()

        thread = threading.Thread(target=hold_chain)
        thread.start()
        try:
            assert holding.wait(timeout=10)
            with factory() as second:
                second.execute(text("SET LOCAL lock_timeout = '750ms'"))
                write_event(second, bid, estimator, action="waits")
                try:
                    second.commit()
                    blocked.append(False)
                except DBAPIError as exc:
                    blocked.append("lock timeout" in str(exc).lower())
                    second.rollback()
        finally:
            release.set()
            thread.join(timeout=15)

        assert blocked == [True]
