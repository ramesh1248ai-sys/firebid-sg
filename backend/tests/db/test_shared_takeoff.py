# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""One project, two bids, one verified takeoff (FR-BID-04; ADR-012).

A project is bid to two main contractors. Alice's bid takes the drawings off, verifies and
approves G1, and publishes the takeoff to the project. Bob's bid adopts it. Each bid keeps
its own client BOQ and prices: Bob, who is on his bid only, sees the shared takeoff and
nothing of Alice's client-specific data, in the query layer and under row-level security.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from firebid.auth.provisioning import Principal
from firebid.db.models.commercial import Boq, BoqLine, ClientBoq
from firebid.db.models.core import Bid, BidMember, Organisation
from firebid.db.models.documents import Document
from firebid.db.models.takeoff import QtoItem
from firebid.db.models.workflow import Approval
from firebid.domain.state_machines import Role
from firebid.domain.values import Money
from firebid.services import delta, qto, review_actions, shared_takeoff
from tests.db.test_row_level_security import rows_visible
from tests.db.test_sheet_views import SignIn, app, member, sign_in  # noqa: F401

pytestmark = pytest.mark.req("FR-BID-04")

HEADS, PIPE = "QTO-000001", "QTO-000002"


def takeoff(session: Session, bid: Bid) -> dict[str, QtoItem]:
    """A small takeoff of the bid's own, as the engine leaves it: proposed, with sources."""
    made = {}
    for human_id, kind, description, unit, quantity in (
        (HEADS, "sprinkler", "Sprinkler, pendent", "no", "16"),
        (PIPE, "branch", "Pipe, DN50, branch", "m", "72.000"),
    ):
        item = QtoItem(
            bid_id=bid.id,
            human_id=human_id,
            item_type="pipe" if unit == "m" else "sprinkler_pendent",
            classification=kind,
            description=description,
            unit=unit,
            net_quantity=Decimal(quantity),
            calculation_method="count" if unit == "no" else "centreline_length",
            confidence=0.9,
            level="L05",
            state="proposed",
            item_key=f"key-{human_id}",
            inputs_hash=f"hash-{human_id}",
            derivation={
                "members": [{"kind": "detection", "sheet": "FP-L05-201", "x": 10.0, "y": 20.0}],
                "sources": [
                    {
                        "sheet_number": "FP-L05-201",
                        "revision": "R01",
                        "sheet_id": "00000000-0000-0000-0000-000000000001",
                        "document_id": "00000000-0000-0000-0000-000000000002",
                    }
                ],
                "geometry": [{"sheet": "FP-L05-201", "x": 10.0, "y": 20.0}],
                "rule_set_version": "drop_length@1",
            },
        )
        session.add(item)
        made[human_id] = item
    session.flush()
    return made


def approve(session: Session, bid: Bid, senior: Principal) -> None:
    """G1 on the bid, as `qto.approve_g1` records it (these bids have no drawings to read)."""
    items = qto.live_items(session, bid.id)
    review_actions.accept(session, bid.id, [i.id for i in items], senior.actor())
    approval = Approval(
        bid_id=bid.id,
        gate="G1",
        decision="approved",
        approver_id=senior.user_id,
        approver_role="senior_estimator",
        decided_at=datetime.now(UTC),
        snapshot_hash=qto.snapshot_hash(qto.live_items(session, bid.id)),
    )
    session.add(approval)
    session.flush()


def client_data(session: Session, bid: Bid, client_name: str, price: str) -> None:
    """What is the bid's alone: its client's BOQ document, and a price."""
    document = Document(
        bid_id=bid.id,
        filename=f"{client_name} BOQ.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        sha256=(client_name.encode().hex() * 8)[:64],
        byte_size=1,
        kind="xlsx",
        storage_key=f"bids/{bid.id}/boq.xlsx",
        state="done",
    )
    session.add(document)
    session.flush()
    session.add(ClientBoq(bid_id=bid.id, document_id=document.id, status="read"))
    boq = Boq(bid_id=bid.id, kind="company", version=1, is_current=True)
    session.add(boq)
    session.flush()
    # A lump sum: the estimator's own figure for this client, with no rate entry behind it.
    session.add(
        BoqLine(
            bid_id=bid.id,
            boq_id=boq.id,
            description="Testing and commissioning",
            unit="sum",
            quantity=Decimal(1),
            is_lump_sum=True,
            amount=Money(Decimal(price)),
        )
    )
    session.flush()


@pytest.fixture
def project(
    session: Session, organisation: Organisation, bid: Bid
) -> tuple[Bid, Bid, Principal, Principal]:
    """Alice's bid (the fixture's) and Bob's, in one project; each on their own bid only."""
    other = Bid(
        organisation_id=organisation.id,
        project_id=bid.project_id,
        human_id="BID-2026-015",
        client_name="Second Main Contractor Pte Ltd",
        tender_reference="MC2/2026/FP/007",
        submission_deadline=bid.submission_deadline,
    )
    session.add(other)
    session.commit()
    alice = member(session, organisation, bid, "alice", Role.SENIOR_ESTIMATOR)
    bob = member(session, organisation, other, "bob", Role.SENIOR_ESTIMATOR)
    takeoff(session, bid)
    approve(session, bid, alice)
    client_data(session, bid, "First", "4200.00")
    client_data(session, other, "Second", "3950.00")
    session.commit()
    return bid, other, alice, bob


def live(session: Session, bid: Bid) -> dict[str, QtoItem]:
    return {item.description: item for item in qto.live_items(session, bid.id)}


class TestPublishing:
    def test_a_takeoff_is_published_only_once_g1_is_approved(
        self, session: Session, organisation: Organisation, bid: Bid
    ) -> None:
        senior = member(session, organisation, bid, "sam", Role.SENIOR_ESTIMATOR)
        takeoff(session, bid)

        with pytest.raises(shared_takeoff.SharedTakeoffError, match="once G1 is approved"):
            shared_takeoff.publish(session, bid, senior.actor())

    def test_what_is_published_is_quantities_and_drawing_references(
        self, session: Session, project: tuple[Bid, Bid, Principal, Principal]
    ) -> None:
        first, _, alice, _ = project

        shared = shared_takeoff.publish(session, first, alice.actor(), "issued for tender")

        assert (shared.version, shared.source_bid_human_id) == (1, first.human_id)
        assert shared.published_by == "Alice" and len(shared.items) == 2
        heads = next(dict(i) for i in shared.items if i["human_id"] == HEADS)
        assert heads["net_quantity"] == "16.000" and heads["verified_by"] == "Alice"
        assert heads["derivation"]["sources"][0]["sheet_number"] == "FP-L05-201"  # type: ignore[index]
        published = str(shared.items)
        assert "4200" not in published and "First BOQ" not in published

    def test_a_reopened_g1_stops_publishing(
        self, session: Session, project: tuple[Bid, Bid, Principal, Principal]
    ) -> None:
        first, _, alice, _ = project
        approval = delta.approved_g1(session, first.id)
        assert approval is not None
        approval.reopened_at = datetime.now(UTC)
        session.flush()

        with pytest.raises(shared_takeoff.SharedTakeoffError, match="while it holds"):
            shared_takeoff.publish(session, first, alice.actor())


class TestAdopting:
    def test_the_shared_baseline_is_visible_to_both_bids(
        self, session: Session, project: tuple[Bid, Bid, Principal, Principal]
    ) -> None:
        first, second, alice, bob = project
        shared_takeoff.publish(session, first, alice.actor())

        outcome = shared_takeoff.adopt(session, second, bob.actor())

        mine, theirs = live(session, second), live(session, first)
        assert len(outcome.created) == 2
        assert {d: i.net_quantity for d, i in mine.items()} == {
            d: i.net_quantity for d, i in theirs.items()
        }
        heads = mine["Sprinkler, pendent"]
        assert heads.state == "verified" and heads.verified_by_id == bob.user_id
        reference = dict(heads.derivation)["shared"]
        assert reference["source_bid"] == first.human_id  # type: ignore[index]
        assert reference["verified_by"] == "Alice"  # type: ignore[index]
        assert qto.completeness(session, second.id, write=False) == []
        assert shared_takeoff.status(session, second)["adopted_versions"] == [1]

    def test_recomputing_the_adopting_bid_leaves_the_adopted_items_alone(
        self, session: Session, project: tuple[Bid, Bid, Principal, Principal]
    ) -> None:
        first, second, alice, bob = project
        shared_takeoff.publish(session, first, alice.actor())
        shared_takeoff.adopt(session, second, bob.actor())

        outcome = qto.recompute(session, second.id)

        assert (outcome.created, outcome.superseded) == (0, 0)
        assert {i.state for i in qto.live_items(session, second.id)} == {"verified"}

    def test_a_bid_specific_edit_does_not_change_the_other_bid(
        self, session: Session, project: tuple[Bid, Bid, Principal, Principal]
    ) -> None:
        first, second, alice, bob = project
        shared = shared_takeoff.publish(session, first, alice.actor())
        shared_takeoff.adopt(session, second, bob.actor())
        heads = live(session, second)["Sprinkler, pendent"]

        review_actions.edit(
            session, second.id, heads.id, bob.actor(), "not_in_scope", quantity=Decimal(12)
        )

        assert live(session, second)["Sprinkler, pendent"].net_quantity == Decimal(12)
        assert live(session, first)["Sprinkler, pendent"].net_quantity == Decimal(16)
        published = next(dict(i) for i in shared.items if i["human_id"] == HEADS)
        assert published["net_quantity"] == "16.000"
        assert shared_takeoff.status(session, second)["edited_here"] == [heads.human_id]

    def test_a_later_version_updates_what_was_not_edited_and_keeps_what_was(
        self, session: Session, project: tuple[Bid, Bid, Principal, Principal]
    ) -> None:
        first, second, alice, bob = project
        shared_takeoff.publish(session, first, alice.actor())
        shared_takeoff.adopt(session, second, bob.actor())
        mine = live(session, second)
        review_actions.edit(
            session,
            second.id,
            mine["Sprinkler, pendent"].id,
            bob.actor(),
            "not_in_scope",
            quantity=Decimal(12),
        )
        # An addendum in the first bid: both items change there, and it publishes again.
        for item in qto.live_items(session, first.id):
            item.net_quantity += 2
            item.inputs_hash = f"{item.inputs_hash}-r2"
        session.flush()
        second_version = shared_takeoff.publish(session, first, alice.actor())
        assert shared_takeoff.status(session, second)["behind"] is True

        outcome = shared_takeoff.adopt(session, second, bob.actor())

        after = live(session, second)
        assert second_version.version == 2
        assert outcome.updated == [mine["Pipe, DN50, branch"].human_id]
        assert after["Pipe, DN50, branch"].net_quantity == Decimal("74.000")
        assert after["Pipe, DN50, branch"].version == 2
        [kept] = outcome.kept
        assert (kept["this_bid"], kept["published"]) == ("12.000", "18.000")
        assert after["Sprinkler, pendent"].net_quantity == Decimal(12)

    def test_a_bid_with_its_own_takeoff_does_not_adopt_another(
        self, session: Session, project: tuple[Bid, Bid, Principal, Principal]
    ) -> None:
        first, second, alice, bob = project
        shared_takeoff.publish(session, first, alice.actor())
        takeoff(session, second)

        with pytest.raises(shared_takeoff.SharedTakeoffError, match="not both"):
            shared_takeoff.adopt(session, second, bob.actor())
        with pytest.raises(shared_takeoff.SharedTakeoffError, match="already its own"):
            shared_takeoff.adopt(session, first, alice.actor())


class TestWhatStaysWithEachBid:
    def test_row_level_security_shares_the_takeoff_and_nothing_else(
        self,
        session: Session,
        app_role_engine: Engine,
        project: tuple[Bid, Bid, Principal, Principal],
    ) -> None:
        first, second, alice, bob = project
        shared_takeoff.publish(session, first, alice.actor())
        shared_takeoff.adopt(session, second, bob.actor())
        session.commit()

        def sees(person: Principal, table: str, bid: Bid) -> int:
            return rows_visible(
                app_role_engine,
                person.user_id,
                f"SELECT count(*) FROM {table} WHERE bid_id = :bid",  # noqa: S608
                {"bid": bid.id},
            )

        # The published takeoff is the project's: both see it.
        for person in (alice, bob):
            assert (
                rows_visible(app_role_engine, person.user_id, "SELECT count(*) FROM shared_takeoff")
                == 1
            )
        # Each sees their own bid's client BOQ, bill, prices and documents, and none of the
        # other's; nor the other bid's takeoff rows themselves.
        for table in ("client_boq", "boq", "boq_line", "document", "qto_item"):
            assert sees(bob, table, second) >= 1, table
            assert sees(bob, table, first) == 0, table
            assert sees(alice, table, first) >= 1, table
            assert sees(alice, table, second) == 0, table

    def test_someone_on_neither_bid_sees_no_shared_takeoff(
        self,
        session: Session,
        organisation: Organisation,
        app_role_engine: Engine,
        project: tuple[Bid, Bid, Principal, Principal],
    ) -> None:
        first, _, alice, _ = project
        shared_takeoff.publish(session, first, alice.actor())
        elsewhere = Bid(
            organisation_id=organisation.id,
            project_id=first.project_id,
            human_id="BID-2026-016",
            client_name="Third",
            tender_reference="T/3",
            submission_deadline=first.submission_deadline,
        )
        session.add(elsewhere)
        session.commit()
        stranger = member(session, organisation, elsewhere, "carol", Role.ESTIMATOR)
        session.execute(
            BidMember.__table__.delete().where(BidMember.user_id == stranger.user_id)  # type: ignore[attr-defined]
        )
        session.commit()

        assert (
            rows_visible(app_role_engine, stranger.user_id, "SELECT count(*) FROM shared_takeoff")
            == 0
        )

    def test_the_api_refuses_the_other_bid_s_boq_and_prices(
        self,
        session: Session,
        project: tuple[Bid, Bid, Principal, Principal],
        sign_in: SignIn,
    ) -> None:
        first, second, alice, bob = project
        shared_takeoff.publish(session, first, alice.actor())
        session.commit()
        client = sign_in(bob)

        adopted = client.post(f"/bids/{second.id}/shared-takeoff/adopt", json={})
        status = client.get(f"/bids/{second.id}/shared-takeoff").json()

        assert adopted.status_code == 200, adopted.text
        assert len(adopted.json()["created"]) == 2
        assert status["adopted_items"] == 2 and status["published"][0]["mine"] is False
        for path in ("boq", "qto/items", "shared-takeoff", "client-boq", "pricing/totals"):
            assert client.get(f"/bids/{first.id}/{path}").status_code in (403, 404), path

    def test_a_member_of_both_bids_sees_both(
        self,
        session: Session,
        app_role_engine: Engine,
        project: tuple[Bid, Bid, Principal, Principal],
    ) -> None:
        _, second, alice, _ = project
        session.add(BidMember(bid_id=second.id, user_id=alice.user_id, role="senior_estimator"))
        session.commit()

        seen = rows_visible(app_role_engine, alice.user_id, "SELECT count(*) FROM client_boq")

        assert seen == 2
        assert session.execute(select(ClientBoq)).scalars().all()
