# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""Design development through the database and the API (P1-12, FR-DSN-01 to 04).

A design-intent sheet goes through the parse pipeline as an uploaded drawing does. Its
design basis is read; nothing is laid out until a named person confirms the criterion; the
proposed heads and pipes are then stored as proposals marked `designed`, counted by the
takeoff as items of their own, and kept when the sheet is detected again.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from firebid.db.models.audit import AuditEvent
from firebid.db.models.core import Bid, Organisation
from firebid.db.models.design import SheetDesign
from firebid.db.models.takeoff import DetectedObject, PipeRun
from firebid.domain.actors import Actor
from firebid.domain.state_machines import Role
from firebid.evals import synthetic_design
from firebid.services import design, qto
from firebid.services.detection import detect_bid
from firebid.storage.object_store import MemoryObjectStore
from tests.db.test_sheet_views import SignIn, app, member, sign_in  # noqa: F401
from tests.db.test_symbol_mapping import from_consultant, no_tiles, read, store  # noqa: F401

SENIOR = Actor(label="Tan Wei Ming", roles=frozenset({"senior_estimator"}))
NOT_DRAWN = "[proposed layout, not drawn]"


def sheet(
    session: Session, bid: Bid, store: MemoryObjectStore, *, with_dimensions: bool = True
) -> SheetDesign:
    from_consultant(session, bid, "SYNTHETIC CONSULTANTS PTE LTD")
    read(
        session,
        bid,
        store,
        "FP-L10-01",
        synthetic_design.design_intent_plan(with_dimensions=with_dimensions),
    )
    (row,) = design.read_basis(session, store, bid.id)
    # `read` does what the parse job does; the jobs it queued have nothing left to do.
    session.execute(text("DELETE FROM procrastinate_jobs"))
    session.commit()
    return row


def designed(session: Session, bid: Bid) -> tuple[list[DetectedObject], list[PipeRun]]:
    objects = list(
        session.execute(
            select(DetectedObject).where(
                DetectedObject.bid_id == bid.id, DetectedObject.extraction_method == "designed"
            )
        ).scalars()
    )
    runs = list(
        session.execute(
            select(PipeRun).where(PipeRun.bid_id == bid.id, PipeRun.origin == "designed")
        ).scalars()
    )
    return objects, runs


def confirmed(session: Session, bid: Bid, store: MemoryObjectStore) -> SheetDesign:
    row = sheet(session, bid, store)
    design.confirm(session, bid.id, [row.sheet_id], SENIOR, key="note_1")
    design.lay_out_bid(session, store, bid.id)
    session.commit()
    return row


@pytest.mark.req("FR-DSN-01")
class TestDesignBasis:
    def test_the_basis_is_read_and_nothing_is_laid_out(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        row = sheet(session, bid, store)

        assert row.state == "proposed"
        assert row.design_intent and row.intent_quote == synthetic_design.INTENT_NOTE
        assert row.drawn_heads == 0
        assert [c["key"] for c in row.criteria] == ["note_1", "note_2", "oh_default"]
        assert row.criterion is None
        assert designed(session, bid) == ([], [])

    def test_a_layout_is_refused_until_a_person_confirms(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        row = sheet(session, bid, store)

        with pytest.raises(design.DesignError, match="a person must confirm"):
            design.lay_out(session, store, row)

    def test_confirming_names_the_person_and_is_audited(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        row = sheet(session, bid, store)

        outcome = design.confirm(session, bid.id, [row.sheet_id], SENIOR, key="note_1")
        session.commit()

        assert [r.id for r in outcome.confirmed] == [row.id] and outcome.skipped == {}
        assert (row.state, row.confirmed_by) == ("confirmed", "Tan Wei Ming")
        assert row.criterion is not None and "MAXIMUM SPACING" in str(row.criterion["source"])
        event = session.execute(
            select(AuditEvent).where(AuditEvent.action == "design basis: confirm")
        ).scalar_one()
        assert event.entity_id == str(row.id)
        queued = session.execute(
            text("SELECT count(*) FROM procrastinate_jobs WHERE task_name = 'design.layout'")
        ).scalar_one()
        assert queued == 1

    def test_a_sheet_whose_scale_is_not_verified_is_blocked_and_says_why(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        row = sheet(session, bid, store, with_dimensions=False)

        assert row.state == "blocked"
        assert row.note is not None and "FR-VIS-05" in row.note
        outcome = design.confirm(session, bid.id, [row.sheet_id], SENIOR, key="note_1")
        assert outcome.confirmed == [] and row.sheet_id in outcome.skipped

    def test_a_person_may_enter_a_criterion_the_notes_do_not_state(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        row = sheet(session, bid, store)

        design.confirm(
            session, bid.id, [row.sheet_id], SENIOR, entered=design.Entered(9.0, (3000, 3000))
        )

        assert row.criterion is not None
        assert row.criterion["source"] == "entered by Tan Wei Ming"

    def test_reading_the_basis_again_keeps_a_confirmation(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        row = confirmed(session, bid, store)
        before = designed(session, bid)

        design.read_basis(session, store, bid.id)
        session.commit()

        assert row.state == "confirmed"
        assert len(designed(session, bid)[0]) == len(before[0])


@pytest.mark.req("FR-DSN-03")
class TestLayout:
    def test_heads_and_pipes_are_stored_as_proposals_with_the_rule_they_follow(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        row = confirmed(session, bid, store)
        objects, runs = designed(session, bid)

        heads = [o for o in objects if o.kind == "object"]
        drops = [o for o in objects if o.kind == "drop"]
        assert len(heads) == row.totals["heads"]
        assert heads
        assert len(drops) == len(heads)
        assert {o.state for o in objects} | {r.state for r in runs} == {"proposed"}
        for head in heads:
            rule = head.source_ref["rule"]
            assert (rule["rule_key"], rule["rule_version"]) == ("sprinkler_layout", 1)  # type: ignore[index]
            assert head.view_id == row.view_id and head.level == "L10"
            assert head.grid_reference is not None or "grid_reference" in head.gaps
        lengths: dict[int, float] = {}
        for run in runs:
            assert run.nominal_dn is not None and run.length_mm is not None
            lengths[run.nominal_dn] = lengths.get(run.nominal_dn, 0.0) + run.length_mm
        # Read back from the database, where a JSON object's keys are text.
        session.refresh(row)
        stated: dict[str, float] = dict(row.totals["range_pipe_m"])  # type: ignore[call-overload]
        assert {str(dn): round(mm / 1000, 1) for dn, mm in lengths.items()} == stated
        assert row.rule_version == 1
        named = {space["name"]: space for space in row.spaces}
        assert named["WARD A"]["heads"] == 12 and named["STORE"]["heads"] == 1

    def test_laying_out_again_gives_the_same_proposal(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        confirmed(session, bid, store)
        before = sorted((o.kind, str(o.geometry_ref)) for o in designed(session, bid)[0])

        design.lay_out_bid(session, store, bid.id)
        session.commit()

        assert sorted((o.kind, str(o.geometry_ref)) for o in designed(session, bid)[0]) == before

    def test_detecting_the_sheet_again_keeps_the_proposal(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        confirmed(session, bid, store)
        before = len(designed(session, bid)[0]), len(designed(session, bid)[1])

        detect_bid(session, store, bid.id)
        session.commit()

        assert (len(designed(session, bid)[0]), len(designed(session, bid)[1])) == before

    def test_a_head_a_person_rejected_stays_rejected_when_laid_out_again(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        confirmed(session, bid, store)
        head = next(o for o in designed(session, bid)[0] if o.kind == "object")
        place = head.geometry_ref
        head.state = "rejected"
        session.commit()

        design.lay_out_bid(session, store, bid.id)
        session.commit()

        again = [o for o in designed(session, bid)[0] if o.geometry_ref == place]
        assert again and {o.state for o in again} == {"rejected"}

    def test_withdrawing_removes_the_proposal(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        row = confirmed(session, bid, store)

        design.withdraw(session, row, SENIOR, "heads are on the addendum drawings")
        session.commit()

        assert designed(session, bid) == ([], [])
        assert (row.state, row.criterion, row.totals) == ("proposed", None, {})


@pytest.mark.req("FR-DSN-04")
class TestTakeoff:
    def test_proposed_quantities_are_items_of_their_own_with_complete_evidence(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        row = confirmed(session, bid, store)

        outcome = qto.recompute(session, bid.id)
        session.commit()

        items = qto.live_items(session, bid.id)
        proposed = [item for item in items if item.description.endswith(NOT_DRAWN)]
        heads = [item for item in proposed if item.unit == "no"]
        assert sum(item.net_quantity for item in heads) == Decimal(row.totals["heads"])  # type: ignore[arg-type]
        assert {item.calculation_method for item in proposed} == {"rule_derived"}
        assert {(item.rule_key, item.rule_version) for item in proposed} == {
            ("sprinkler_layout", 1)
        }
        assert {item.state for item in proposed} == {"proposed"}
        assert outcome.incomplete == []
        drops = [item for item in items if item.classification == "drop"]
        assert drops and drops[0].rule_key == "drop_length"

    def test_recomputing_changes_nothing(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        confirmed(session, bid, store)
        qto.recompute(session, bid.id)
        session.commit()

        outcome = qto.recompute(session, bid.id)

        assert (outcome.created, outcome.superseded) == (0, 0)


@pytest.mark.req("FR-DSN-01")
class TestApi:
    def test_the_basis_is_listed_and_only_some_roles_may_confirm(
        self,
        session: Session,
        organisation: Organisation,
        bid: Bid,
        store: MemoryObjectStore,
        sign_in: SignIn,
    ) -> None:
        row = sheet(session, bid, store)
        estimator = member(session, organisation, bid, "esther", Role.ESTIMATOR)
        senior = member(session, organisation, bid, "wei ming", Role.SENIOR_ESTIMATOR)
        body = {"sheet_ids": [str(row.sheet_id)], "key": "note_1"}

        listed = sign_in(estimator).get(f"/bids/{bid.id}/design")
        assert listed.status_code == 200, listed.text
        (found,) = listed.json()["sheets"]
        assert (found["sheet_number"], found["state"]) == ("FP-L10-01", "proposed")
        assert listed.json()["rule_status"] == "to be confirmed"

        refused = sign_in(estimator).post(f"/bids/{bid.id}/design/confirm", json=body)
        assert refused.status_code == 403

        accepted = sign_in(senior).post(f"/bids/{bid.id}/design/confirm", json=body)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json() == {"confirmed": [str(row.sheet_id)], "skipped": {}}

    def test_another_bid_s_design_is_not_found(
        self,
        session: Session,
        organisation: Organisation,
        bid: Bid,
        second_bid: Bid,
        store: MemoryObjectStore,
        sign_in: SignIn,
    ) -> None:
        sheet(session, bid, store)
        outsider = member(session, organisation, second_bid, "olive", Role.SENIOR_ESTIMATOR)

        assert sign_in(outsider).get(f"/bids/{bid.id}/design").status_code == 404
