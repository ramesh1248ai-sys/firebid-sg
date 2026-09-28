# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""The verification workbench's data (P1-08): the overlay, the queue, actions and G1.

The tender is P1-07's: a general arrangement, an enlarged plan of its riser area and a
riser schematic, detected and taken off.
"""

from __future__ import annotations

import uuid
from collections import Counter
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.models.audit import AuditEvent
from firebid.db.models.core import Bid, Organisation
from firebid.db.models.documents import SheetRevision
from firebid.db.models.review import CorrectionEvent
from firebid.db.models.takeoff import DuplicateGroup, QtoItem
from firebid.domain.actors import Actor
from firebid.services import qto, review, review_actions
from tests.db.test_qto import PENDENT, estimator, items, tender  # noqa: F401
from tests.db.test_sheet_views import SignIn, app, sign_in  # noqa: F401
from tests.db.test_symbol_mapping import no_tiles, store  # noqa: F401


def sheet_id(session: Session, number: str) -> str:
    revision = session.execute(
        select(SheetRevision).where(SheetRevision.sheet_number == number)
    ).scalar_one()
    return str(revision.sheet_id)


@pytest.mark.req("FR-REV-01")
class TestOverlay:
    def test_every_mark_carries_its_item_status_and_band(
        self, session: Session, organisation: Organisation, tender: Bid, sign_in: SignIn
    ) -> None:
        _, principal = estimator(session, organisation, tender)
        general = sheet_id(session, "FP-L05-201")

        marks = (
            sign_in(principal)
            .get(f"/bids/{tender.id}/qto/overlay", params={"sheet_id": general})
            .json()
        )

        heads = [m for m in marks if m["object_type"] == "sprinkler_pendent"]
        assert len(heads) == 16
        pendent = items(session, tender)[PENDENT]
        assert {m["item_id"] for m in heads} == {str(pendent.id)}
        assert {m["status"] for m in heads} == {"proposed"}
        assert {m["band"] for m in marks} <= {"high", "medium", "low"}
        runs = [m for m in marks if m["kind"] == "run"]
        assert runs and all(len(m["points"]) >= 2 and m["item_id"] for m in runs)
        assert all(m["box"][0] <= m["box"][2] and m["box"][1] <= m["box"][3] for m in marks)

    def test_what_another_sheet_counts_is_shown_as_a_duplicate(
        self, session: Session, tender: Bid
    ) -> None:
        enlarged = sheet_id(session, "FP-L05-301")

        marks = review.overlay(session, tender.id, enlarged)  # type: ignore[arg-type]

        statuses = Counter(m.status for m in marks if m.kind == "detection")
        assert statuses["duplicate"] >= 4
        assert all(m.item_id is None for m in marks if m.status == "duplicate")

    def test_an_item_says_where_its_evidence_is(self, session: Session, tender: Bid) -> None:
        pendent = items(session, tender)[PENDENT]

        boxes = review.evidence_boxes(pendent)

        assert [b["sheet_id"] for b in boxes] == [sheet_id(session, "FP-L05-201")]
        x0, y0, x1, y1 = boxes[0]["box"]
        assert x1 > x0 and y1 > y0


# --- The queue (FR-REV-02) ------------------------------------------------------------------


def crafted(session: Session, bid: Bid) -> dict[str, QtoItem]:
    """Items whose risk is known by hand: (1 - confidence) x quantity x class weight."""
    made = {}
    for human_id, classification, quantity, confidence, level in (
        ("QTO-000101", "valve", "2", 0.5, "L01"),  # 0.5 x 2 x 25 = 25
        ("QTO-000102", "sprinkler", "100", 0.9, "L01"),  # 0.1 x 100 x 1 = 10
        ("QTO-000103", "branch", "40", 0.6, "L02"),  # 0.4 x 40 x 1 = 16
        ("QTO-000104", "main", "10", 0.99, "L02"),  # 0.01 x 10 x 2 = 0.2
        ("QTO-000105", "sprinkler", "8", 0.2, "L02"),  # 0.8 x 8 x 1 = 6.4
    ):
        item = QtoItem(
            bid_id=bid.id,
            human_id=human_id,
            item_type="pipe" if classification in ("branch", "main") else classification,
            classification=classification,
            description=human_id,
            unit="m" if classification in ("branch", "main") else "no",
            net_quantity=Decimal(quantity),
            calculation_method="count",
            confidence=confidence,
            level=level,
            state="proposed",
        )
        session.add(item)
        made[human_id] = item
    session.flush()
    return made


@pytest.mark.req("FR-REV-02")
class TestQueue:
    def test_riskiest_first_by_the_formula(self, session: Session, bid: Bid) -> None:
        crafted(session, bid)

        rows = review.queue(session, bid.id)

        assert [r.item.human_id for r in rows] == [
            "QTO-000101",
            "QTO-000103",
            "QTO-000102",
            "QTO-000105",
            "QTO-000104",
        ]
        assert [round(r.risk, 3) for r in rows] == [25.0, 16.0, 10.0, 6.4, 0.2]
        assert rows[0].impact == pytest.approx(50.0)

    def test_decided_items_go_to_the_end(self, session: Session, bid: Bid) -> None:
        made = crafted(session, bid)
        made["QTO-000101"].state = "verified"
        session.flush()

        rows = review.queue(session, bid.id)

        assert rows[-1].item.human_id == "QTO-000101"

    def test_filters(self, session: Session, bid: Bid) -> None:
        crafted(session, bid)

        by_level = review.queue(session, bid.id, level="L02")
        by_type = review.queue(session, bid.id, item_type="sprinkler")
        by_status = review.queue(session, bid.id, status="verified")

        assert {r.item.human_id for r in by_level} == {"QTO-000103", "QTO-000104", "QTO-000105"}
        assert {r.item.human_id for r in by_type} == {"QTO-000102", "QTO-000105"}
        assert by_status == []


# --- Actions (FR-REV-03) and corrections (FR-REV-06) ----------------------------------------


def audit_actions(session: Session, entity_id: object) -> list[str]:
    return [
        e.action
        for e in session.execute(
            select(AuditEvent)
            .where(AuditEvent.entity_id == str(entity_id))
            .order_by(AuditEvent.occurred_at)
        ).scalars()
    ]


@pytest.mark.req("FR-REV-03")
class TestActions:
    def test_bulk_accept_verifies_each_item_through_the_state_machine(
        self, session: Session, organisation: Organisation, tender: Bid
    ) -> None:
        actor, _ = estimator(session, organisation, tender)
        live = qto.live_items(session, tender.id)

        action = review_actions.accept(session, tender.id, [i.id for i in live], actor)

        assert {i.state for i in qto.live_items(session, tender.id)} == {"verified"}
        assert len(action.entries) == len(live)
        assert all(i.verified_by_id == actor.id for i in live)
        assert "QTO item: verify" in audit_actions(session, live[0].id)

    def test_edit_and_reject_need_a_reason(
        self, session: Session, organisation: Organisation, tender: Bid
    ) -> None:
        actor, _ = estimator(session, organisation, tender)
        pendent = items(session, tender)[PENDENT]

        with pytest.raises(review_actions.ReviewError, match="reason"):
            review_actions.reject(session, tender.id, [pendent.id], actor, None)
        with pytest.raises(review_actions.ReviewError, match="unknown reason"):
            review_actions.edit(
                session, tender.id, pendent.id, actor, "because", quantity=Decimal(15)
            )

    def test_an_edit_is_verified_as_edited_and_recorded_as_a_correction(
        self, session: Session, organisation: Organisation, tender: Bid
    ) -> None:
        actor, _ = estimator(session, organisation, tender)
        pendent = items(session, tender)[PENDENT]

        action = review_actions.edit(
            session,
            tender.id,
            pendent.id,
            actor,
            "wrong_quantity",
            "one head is a test valve",
            quantity=Decimal(15),
            attributes={"finish": "chrome"},
        )

        assert (pendent.state, pendent.net_quantity) == ("verified", Decimal("15.000"))
        finish: dict[str, str] = dict(pendent.attributes["finish"])  # type: ignore[call-overload]
        assert finish["source"] == "edited by Esther"
        assert audit_actions(session, pendent.id)[-3:] == [
            "QTO item: edit",
            "QTO item: edit values",
            "QTO item: verify edit",
        ]
        [event] = session.execute(select(CorrectionEvent)).scalars()
        assert (event.kind, event.reason_code, event.action_id) == (
            "edit",
            "wrong_quantity",
            action.id,
        )
        assert event.before["net_quantity"] == "16.000"
        assert event.after is not None and event.after["net_quantity"] == "15.000"
        assert event.detector_version and event.calibration_version
        assert event.detector_method == "cad_block"
        assert event.data_policy == "derived-labels-only"
        record = qto.evidence_for(session, tender, pendent)
        assert record.calculation_note and "edited by Esther" in record.calculation_note

    def test_a_length_is_edited_by_measuring_again(
        self, session: Session, organisation: Organisation, tender: Bid
    ) -> None:
        actor, _ = estimator(session, organisation, tender)
        branch = items(session, tender)["Pipe, DN50, branch"]

        with pytest.raises(review_actions.ReviewError, match="re-measure"):
            review_actions.edit(
                session, tender.id, branch.id, actor, "wrong_quantity", quantity=Decimal(70)
            )

    def test_undo_puts_every_item_back(
        self, session: Session, organisation: Organisation, tender: Bid
    ) -> None:
        actor, _ = estimator(session, organisation, tender)
        pendent = items(session, tender)[PENDENT]
        valve = items(session, tender)["Gate valve, DN150"]
        edited = review_actions.edit(
            session, tender.id, pendent.id, actor, "wrong_quantity", quantity=Decimal(15)
        )
        rejected = review_actions.reject(session, tender.id, [valve.id], actor, "not_in_scope")

        review_actions.undo(session, tender.id, edited.id, actor)
        review_actions.undo(session, tender.id, rejected.id, actor)

        assert (pendent.state, pendent.net_quantity) == ("proposed", Decimal("16.000"))
        assert valve.state == "proposed" and valve.reason_code is None
        with pytest.raises(review_actions.ReviewError, match="already been undone"):
            review_actions.undo(session, tender.id, edited.id, actor)

    def test_undo_is_refused_once_the_item_has_moved_on(
        self, session: Session, organisation: Organisation, tender: Bid
    ) -> None:
        actor, _ = estimator(session, organisation, tender)
        valve = items(session, tender)["Gate valve, DN150"]
        accepted = review_actions.accept(session, tender.id, [valve.id], actor)
        review_actions.reject(session, tender.id, [valve.id], actor, "not_in_scope")

        with pytest.raises(review_actions.ReviewError, match="changed since"):
            review_actions.undo(session, tender.id, accepted.id, actor)


@pytest.mark.req("FR-REV-03")
@pytest.mark.req("FR-REV-06")
class TestFalseDetections:
    def test_a_head_that_is_not_there_leaves_the_count(
        self, session: Session, organisation: Organisation, tender: Bid
    ) -> None:
        actor, _ = estimator(session, organisation, tender)
        general = sheet_id(session, "FP-L05-201")
        head = next(
            m
            for m in review.overlay(session, tender.id, uuid.UUID(general))
            if m.object_type == "sprinkler_pendent"
        )

        action = review_actions.reject_detections(
            session, tender.id, [uuid.UUID(head.id)], actor, "false_detection", "a light fitting"
        )

        # The head goes, from the enlarged plan too if it is drawn there, and so does its drop.
        assert items(session, tender)[PENDENT].net_quantity == Decimal(15)
        drops = items(session, tender)["Sprinkler drop, DN25 (vertical, not drawn)"]
        assert drops.length is not None and drops.length.mm == 23 * 500
        [event] = session.execute(select(CorrectionEvent)).scalars()
        assert (event.kind, event.detection_id, event.object_type) == (
            "false_detection",
            uuid.UUID(head.id),
            "sprinkler_pendent",
        )
        assert event.detector_version and event.calibration_version

        review_actions.undo(session, tender.id, action.id, actor)

        assert items(session, tender)[PENDENT].net_quantity == Decimal(16)


@pytest.mark.req("FR-REV-06")
def test_corrections_are_read_as_labelled_rows(
    session: Session, organisation: Organisation, tender: Bid
) -> None:
    actor, _ = estimator(session, organisation, tender)
    valve = items(session, tender)["Gate valve, DN150"]
    review_actions.reject(session, tender.id, [valve.id], actor, "not_in_scope", "by others")

    [row] = review_actions.correction_rows(session, tender.id)

    assert row["kind"] == "reject" and row["reason_code"] == "not_in_scope"
    assert row["before"]["state"] == "proposed" and row["after"] is None
    assert row["object_type"] == "gate_valve" and row["detector_version"]
    # Derived labels only: no drawing text travels with it.
    assert "description" not in row["before"]


# --- Coverage and G1 (FR-REV-04) ------------------------------------------------------------


@pytest.mark.req("FR-REV-04")
class TestCoverageAndG1:
    def test_coverage_by_items_and_by_value(
        self, session: Session, organisation: Organisation, tender: Bid
    ) -> None:
        actor, _ = estimator(session, organisation, tender)
        pendent = items(session, tender)[PENDENT]
        valve = items(session, tender)["Gate valve, DN150"]

        before = review.coverage(session, tender.id)
        review_actions.accept(session, tender.id, [pendent.id], actor)
        review_actions.reject(session, tender.id, [valve.id], actor, "not_in_scope")
        after = review.coverage(session, tender.id)

        assert (before["items_verified"], before["items_percent"], before["met"]) == (
            0,
            0.0,
            False,
        )
        # The rejected valve is decided: it leaves the count.
        assert after["items_total"] == before["items_total"] - 1
        assert after["items_verified"] == 1
        assert after["value_verified"] == pytest.approx(16.0)  # 16 heads x weight 1
        assert "no rates" in after["value_basis"]

    def test_g1_lists_coverage_until_everything_is_verified(
        self, session: Session, organisation: Organisation, tender: Bid
    ) -> None:
        actor, _ = estimator(session, organisation, tender)
        name_every_unlisted_symbol(session, tender, actor)
        group = session.execute(
            select(DuplicateGroup).where(DuplicateGroup.status == "unresolved")
        ).scalar_one()
        qto.decide_group(session, group, "confirmed", actor)

        short = qto.g1_blockers(session, tender.id)
        review_actions.accept(
            session, tender.id, [i.id for i in qto.live_items(session, tender.id)], actor
        )
        clear = qto.g1_blockers(session, tender.id)

        assert short.coverage_short and not short.clear
        assert clear.clear, clear
        assert qto.approve_g1(session, tender, actor, "senior_estimator").gate == "G1"

    def test_an_unmapped_symbol_in_scope_blocks_g1(
        self, session: Session, organisation: Organisation, tender: Bid
    ) -> None:
        from firebid.db.models.symbols import LegendEntry
        from firebid.services import symbols

        actor, _ = estimator(session, organisation, tender)
        entry = (
            session.execute(select(LegendEntry).where(LegendEntry.description == "GATE VALVE"))
            .scalars()
            .first()
        )
        assert entry is not None and entry.mapping_lineage_id is not None

        symbols.reject(session, entry.mapping_lineage_id, actor, "not sure", tender.id)

        found = qto.g1_blockers(session, tender.id)
        assert "GATE VALVE" in [u["description"] for u in found.unmapped_symbols]
        assert not found.clear

    def test_a_recurring_unlisted_shape_is_named_once_and_stops_blocking(
        self, session: Session, organisation: Organisation, tender: Bid, sign_in: SignIn
    ) -> None:
        _, principal = estimator(session, organisation, tender)
        before = review.unmapped_in_scope(session, tender.id)
        # The structural grid's bubbles recur on both plans, and no legend explains them.
        assert before and {u["status"] for u in before} == {"no legend"}
        client = sign_in(principal)

        for unlisted in before:
            response = client.post(
                f"/bids/{tender.id}/symbols/unlisted",
                json={"symbol_key": unlisted["symbol_key"], "object_type": "not_an_object"},
            )
            assert response.status_code == 200, response.text

        session.expire_all()
        assert review.unmapped_in_scope(session, tender.id) == []


def name_every_unlisted_symbol(session: Session, bid: Bid, actor: Actor) -> None:
    """What a person does once per consultant: grid bubbles and the like are not objects."""
    from firebid.services import symbols

    for unlisted in review.unmapped_in_scope(session, bid.id):
        symbols.name_unlisted(session, bid.id, unlisted["symbol_key"], "not_an_object", actor)
