# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""Detections through the database: from a parsed drawing to stored proposals (P1-05).

The synthetic installation is read as the parse job reads it. Its legend rows are proposed
by rule; a person confirms each one (the upright, which no rule reads, with its type). Only
then does detection find anything, and what it stores must be complete: method, evidence,
view, grid reference and calibrated confidence, or the reason one is missing (FR-VIS-09).
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from firebid.db.models.core import Bid
from firebid.db.models.drawings import SheetView
from firebid.db.models.symbols import LegendEntry
from firebid.db.models.takeoff import DetectedObject, PipeRun
from firebid.domain.actors import Actor
from firebid.evals.synthetic_network import DESCRIBED, NETWORK, network_plan
from firebid.services import symbols as symbol_service
from firebid.services.detection import detect_bid
from firebid.storage.object_store import MemoryObjectStore
from tests.db.test_sheet_views import app, sign_in  # noqa: F401
from tests.db.test_symbol_mapping import (  # noqa: F401
    _Broken,
    from_consultant,
    no_tiles,
    read,
    store,
)

PERSON = Actor(label="Esther Tan", roles=frozenset({"estimator"}))


def confirm_legend(session: Session, bid: Bid, store: MemoryObjectStore) -> None:
    """A person confirms every legend row as what it is.

    The upright, which no keyword rule reads, waits for the model; here the model is
    unavailable, so it is left for a person with no proposed type, and they give it one.
    """
    for waiting in session.execute(
        select(LegendEntry).where(
            LegendEntry.bid_id == bid.id, LegendEntry.status == "awaiting_model"
        )
    ).scalars():
        symbol_service.propose_with_model(session, store, waiting, _Broken())
    entries = session.execute(select(LegendEntry).where(LegendEntry.bid_id == bid.id)).scalars()
    done: set[uuid.UUID] = set()
    for entry in entries:
        lineage = entry.mapping_lineage_id
        if lineage is None or lineage in done:
            continue
        done.add(lineage)
        key = DESCRIBED[entry.description]
        symbol_service.confirm(
            session,
            lineage,
            PERSON,
            object_type=key,
            attributes={"fitting": "reducer"} if key == "fitting" else None,
        )
    session.commit()


def installation(session: Session, bid: Bid, store: MemoryObjectStore, **options: Any) -> Any:
    from_consultant(session, bid, NETWORK.name)
    document, truth = network_plan(**options)
    read(session, bid, store, "FP-L05-201", document)
    return truth


def stored(session: Session, bid: Bid) -> tuple[list[DetectedObject], list[PipeRun]]:
    objects = list(
        session.execute(select(DetectedObject).where(DetectedObject.bid_id == bid.id)).scalars()
    )
    runs = list(session.execute(select(PipeRun).where(PipeRun.bid_id == bid.id)).scalars())
    return objects, runs


@pytest.mark.req("FR-VIS-03")
class TestFromDrawingToProposals:
    def test_nothing_is_detected_until_its_mapping_is_confirmed(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        installation(session, bid, store)

        objects, runs = stored(session, bid)

        assert objects == [] and runs == []

    def test_confirmed_symbols_give_exact_counts_and_lengths(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        truth = installation(session, bid, store)
        confirm_legend(session, bid, store)

        detect_bid(session, store, bid.id)
        session.commit()
        objects, runs = stored(session, bid)

        counts = Counter(o.object_type for o in objects if o.kind == "object")
        assert dict(counts) == truth.counts
        lengths: dict[int, float] = defaultdict(float)
        for run in runs:
            assert run.nominal_dn is not None
            lengths[run.nominal_dn] += run.length_mm or 0.0
        for dn, expected in truth.lengths.items():
            assert lengths[dn] == pytest.approx(expected, rel=0.005)
        assert Counter(o.kind for o in objects)["drop"] == 24
        assert {o.state for o in objects} | {r.state for r in runs} == {"proposed"}


@pytest.mark.req("FR-VIS-09")
class TestCompleteness:
    def test_every_stored_detection_has_method_evidence_view_grid_and_calibrated_confidence(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        installation(session, bid, store)
        confirm_legend(session, bid, store)
        detect_bid(session, store, bid.id)
        session.commit()

        objects, runs = stored(session, bid)

        assert objects and runs
        for item in objects:
            assert item.extraction_method in ("cad_block", "pdf_shape", "vision", "topology")
            assert item.source_ref and any(item.source_ref.values())
            assert item.view_id is not None and item.grid_reference is not None
            assert item.confidence is not None and item.raw_confidence is not None
            assert item.calibration_version not in (None, "uncalibrated")
            assert item.gaps == {}
        for run in runs:
            assert run.geometry_rows and run.view_id is not None and run.grid_reference
            assert run.calibration_version != "uncalibrated"

    def test_the_database_refuses_a_detection_that_is_neither_located_nor_says_why(
        self, session: Session, bid: Bid
    ) -> None:
        session.add(
            DetectedObject(
                bid_id=bid.id,
                sheet_id=uuid.uuid4(),
                object_type="sprinkler_pendent",
                source_ref={"geometry_rows": [1]},
                extraction_method="pdf_shape",
                confidence=0.9,
                gaps={},
            )
        )

        with pytest.raises(IntegrityError, match="located_or_says_why"):
            session.flush()
        session.rollback()

    def test_a_view_with_no_grid_records_why_there_is_no_grid_reference(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        installation(session, bid, store)
        confirm_legend(session, bid, store)
        for view in session.execute(select(SheetView)).scalars():
            view.grid = None
        session.commit()

        detect_bid(session, store, bid.id)
        session.commit()
        objects, _ = stored(session, bid)

        assert objects
        assert all(o.grid_reference is None for o in objects)
        assert {o.gaps.get("grid_reference") for o in objects} == {
            "the view shows no structural grid"
        }


@pytest.mark.req("FR-VIS-06")
def test_a_conflicting_size_is_stored_unsized_with_its_reason(
    session: Session, bid: Bid, store: MemoryObjectStore
) -> None:
    installation(session, bid, store, conflict=True)
    confirm_legend(session, bid, store)
    detect_bid(session, store, bid.id)
    session.commit()

    _, runs = stored(session, bid)
    [flagged] = [run for run in runs if run.size_status == "conflict"]

    assert flagged.nominal_dn is None
    assert {label["text"] for label in flagged.labels} == {"DN50", "DN65"}
    assert "disagree" in (flagged.size_reason or "")


@pytest.mark.req("FR-VIS-03")
def test_confirming_a_mapping_through_the_api_queues_detection_again(
    session: Session, bid: Bid, store: MemoryObjectStore
) -> None:
    from firebid.api import symbols as symbols_api

    installation(session, bid, store)
    [entry, *_] = session.execute(select(LegendEntry).where(LegendEntry.bid_id == bid.id)).scalars()

    class Context:
        pass

    context: Any = Context()
    context.bid = bid
    principal: Any = type("P", (), {"user_id": uuid.uuid4()})()
    symbols_api._detect_again(session, context, principal)
    session.commit()

    queued = session.execute(
        text(
            "SELECT count(*) FROM procrastinate_jobs WHERE task_name = 'detection.run' "
            "AND args->>'bid_id' = :bid"
        ),
        {"bid": str(bid.id)},
    ).scalar_one()
    assert entry is not None and queued >= 1


@pytest.mark.req("FR-VIS-09")
def test_the_api_lists_detections_least_confident_first(
    session: Session,
    bid: Bid,
    store: MemoryObjectStore,
    sign_in: Any,
    organisation: Any,
) -> None:
    from firebid.domain.state_machines import Role
    from tests.db.test_sheet_views import member

    installation(session, bid, store, conflict=True)
    confirm_legend(session, bid, store)
    detect_bid(session, store, bid.id)
    session.commit()
    person = member(session, organisation, bid, "rita", Role.ESTIMATOR)

    body = sign_in(person).get(f"/bids/{bid.id}/detections").json()

    confidences = [run["confidence"] for run in body["runs"]]
    assert confidences == sorted(confidences)
    assert body["runs"][0]["size_status"] == "conflict", "the flagged run first"
    assert {o["kind"] for o in body["objects"]} == {"object", "riser", "drop"}
    assert all(o["state"] == "proposed" for o in body["objects"])


@pytest.mark.req("NFR-01")
class TestOnlyWhatChanged:
    """Detection is asked for after every mapping decision. It does the work once."""

    def test_detecting_again_with_nothing_changed_leaves_the_rows_alone(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        installation(session, bid, store)
        confirm_legend(session, bid, store)
        first = detect_bid(session, store, bid.id)
        session.commit()
        before = {o.id for o in stored(session, bid)[0]} | {r.id for r in stored(session, bid)[1]}

        second = detect_bid(session, store, bid.id)
        session.commit()

        assert [outcome.unchanged for outcome in first] == [False]
        assert [outcome.unchanged for outcome in second] == [True]
        # The same rows, not new rows for the same symbols: verification keeps its links.
        after = {o.id for o in stored(session, bid)[0]} | {r.id for r in stored(session, bid)[1]}
        assert after == before
        assert (second[0].objects, second[0].runs) == (first[0].objects, first[0].runs)

    def test_a_person_can_have_every_sheet_detected_anyway(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        installation(session, bid, store)
        confirm_legend(session, bid, store)
        detect_bid(session, store, bid.id)
        session.commit()
        before = {o.id for o in stored(session, bid)[0]}

        forced = detect_bid(session, store, bid.id, force=True)
        session.commit()

        assert [outcome.unchanged for outcome in forced] == [False]
        assert {o.id for o in stored(session, bid)[0]}.isdisjoint(before)

    def test_a_changed_mapping_is_detected_again(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        installation(session, bid, store)
        confirm_legend(session, bid, store)
        detect_bid(session, store, bid.id)
        session.commit()
        pendents = sum(1 for o in stored(session, bid)[0] if o.object_type == "sprinkler_pendent")
        assert pendents
        entry = next(
            e
            for e in session.execute(
                select(LegendEntry).where(LegendEntry.bid_id == bid.id)
            ).scalars()
            if DESCRIBED[e.description] == "sprinkler_pendent"
        )
        assert entry.mapping_lineage_id is not None

        symbol_service.reject(session, entry.mapping_lineage_id, PERSON, note="not ours")
        again = detect_bid(session, store, bid.id)
        session.commit()

        assert [outcome.unchanged for outcome in again] == [False]
        assert not any(o.object_type == "sprinkler_pendent" for o in stored(session, bid)[0])

    def test_a_run_asks_for_each_mapping_once_however_many_symbols_share_it(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        from sqlalchemy import event

        installation(session, bid, store)
        confirm_legend(session, bid, store)
        statements: list[str] = []

        def record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
            statements.append(statement)

        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", record)
        try:
            detect_bid(session, store, bid.id)
        finally:
            event.remove(engine, "before_cursor_execute", record)

        mapping_reads = [s for s in statements if "FROM symbol_mapping" in s and "SELECT" in s]
        lineages = {
            e.mapping_lineage_id
            for e in session.execute(
                select(LegendEntry).where(LegendEntry.bid_id == bid.id)
            ).scalars()
        }
        instances = len(stored(session, bid)[0])
        assert instances > len(lineages) * 2, "the plan draws many symbols of few kinds"
        assert len(mapping_reads) <= len(lineages)


@pytest.mark.req("NFR-01")
class TestOneJobForManyDecisions:
    def test_confirming_row_by_row_queues_one_detection(
        self, session: Session, organisation: Any, bid: Bid, store: MemoryObjectStore, sign_in: Any
    ) -> None:
        from firebid.domain.state_machines import Role
        from tests.db.test_sheet_views import member

        installation(session, bid, store)
        session.execute(text("DELETE FROM procrastinate_jobs"))
        session.commit()
        client = sign_in(member(session, organisation, bid, "esther", Role.ESTIMATOR))
        lineages = {
            e.mapping_lineage_id
            for e in session.execute(
                select(LegendEntry).where(LegendEntry.bid_id == bid.id)
            ).scalars()
            if e.mapping_lineage_id is not None
        }
        assert len(lineages) >= 3

        for lineage in list(lineages)[:3]:
            response = client.post(
                f"/bids/{bid.id}/symbols/mappings/{lineage}/confirm",
                json={"object_type": "sprinkler_pendent"},
            )
            assert response.status_code == 200, response.text

        waiting = session.execute(
            text(
                "SELECT count(*) FROM procrastinate_jobs "
                "WHERE task_name = 'detection.run' AND status = 'todo'"
            )
        ).scalar_one()
        assert waiting == 1
        # And every decision was kept: a job that was not queued again took nothing with it.
        session.expire_all()
        for lineage in list(lineages)[:3]:
            mapping = symbol_service.current(session, lineage)
            assert mapping is not None and mapping.state == "confirmed"

    def test_takeoff_asked_for_twice_is_queued_once(self, session: Session, bid: Bid) -> None:
        from firebid.services import qto

        session.execute(text("DELETE FROM procrastinate_jobs"))
        qto.queue_recompute(session, bid.id, None)
        qto.queue_recompute(session, bid.id, None)
        session.commit()

        waiting = session.execute(
            text("SELECT count(*) FROM procrastinate_jobs WHERE task_name = 'qto.recompute'")
        ).scalar_one()
        assert waiting == 1
