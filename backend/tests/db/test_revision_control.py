"""Exactly one Current revision per drawing; superseded and conflicting ones out (FR-DOC-04).

Drawings go the whole real way: ingested, parsed into sheets, title blocks read, and settled
by the revision service. Only `current_sheets` is asked what takeoff may use, because that is
the only question takeoff will ever ask.
"""

from __future__ import annotations

import io
import itertools
from datetime import date

import openpyxl
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.models.core import Bid, Project
from firebid.db.models.documents import Document, SheetRevision, TransmittalEntry
from firebid.db.models.workflow import HumanTask
from firebid.domain.actors import Actor
from firebid.domain.state_machines import SheetRevisionState
from firebid.evals import synthetic
from firebid.ingest.scanning import AlwaysCleanScanner
from firebid.services.classification import classify_in_sandbox
from firebid.services.ingestion import Ingestor
from firebid.services.revisions import current_sheets, read_transmittal, resolve_conflict
from firebid.services.sheets import process_document
from firebid.services.title_blocks import read_title_blocks
from firebid.storage.object_store import MemoryObjectStore

pytestmark = pytest.mark.req("FR-DOC-04")

State = SheetRevisionState
NUMBER = "FP-L05-201"


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


@pytest.fixture(autouse=True)
def no_tiles(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tiles are for the viewer; revision control never looks at them, and they are slow."""
    from firebid.services import sheets

    monkeypatch.setattr(sheets, "_render_low_levels", lambda *_args, **_kwargs: 0)


@pytest.fixture
def person(estimator: Actor) -> Actor:
    return estimator


def arrive(
    session: Session,
    bid: Bid,
    store: MemoryObjectStore,
    revision: str,
    *,
    number: str = NUMBER,
    filename: str | None = None,
) -> SheetRevision:
    """One drawing file, taken the whole way: stored, parsed, read, settled."""
    drawing, _ = synthetic.general_arrangement(sheet_number=number, revision=revision)
    payload = synthetic.dxf_bytes(drawing)
    outcome = Ingestor(session, store, AlwaysCleanScanner(), bid_id=bid.id).ingest(
        filename or f"{number}.dxf", payload
    )
    document = outcome.stored[0]
    sheets = process_document(session, store, document).sheets
    [proposal] = read_title_blocks(session, store, document, sheets)
    return proposal


def states(session: Session, number: str = NUMBER) -> dict[str, str]:
    session.flush()
    return {
        revision.revision_label or "?": revision.state
        for revision in session.execute(
            select(SheetRevision).where(SheetRevision.sheet_number == number)
        ).scalars()
    }


def current_labels(session: Session, bid: Bid) -> list[str]:
    return [revision.revision_label or "?" for revision, _ in current_sheets(session, bid.id)]


def transmittal(*rows: tuple[str, str, date | None]) -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["SYNTHETIC CONSULTANTS PTE LTD"])
    sheet.append(["DRAWING TRANSMITTAL"])
    sheet.append(["DRAWING NO.", "TITLE", "REV", "DATE"])
    for number, revision, issued in rows:
        sheet.append([number, "FIRE SPRINKLER LAYOUT", revision, issued])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def receive_transmittal(
    session: Session, bid: Bid, store: MemoryObjectStore, payload: bytes
) -> Document:
    outcome = Ingestor(session, store, AlwaysCleanScanner(), bid_id=bid.id).ingest(
        "transmittal.xlsx", payload
    )
    document = outcome.stored[0]
    read_transmittal(session, document, payload)
    classify_in_sandbox(session, document, payload)
    return document


class TestSupersededRevisionsAreExcluded:
    """FR-DOC-04's acceptance test: seeded superseded sheets excluded in 100% of cases."""

    @pytest.mark.parametrize(
        "arrival", list(itertools.permutations(["R01", "R02", "R03", "R04"]))[::3]
    )
    def test_the_latest_is_current_whatever_order_they_arrive_in(
        self, session: Session, bid: Bid, store: MemoryObjectStore, arrival: tuple[str, ...]
    ) -> None:
        for revision in arrival:
            arrive(session, bid, store, revision)

        assert states(session) == {
            "R01": "superseded",
            "R02": "superseded",
            "R03": "superseded",
            "R04": "current",
        }
        assert current_labels(session, bid) == ["R04"]

    def test_each_superseded_revision_points_at_what_replaced_it(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        old = arrive(session, bid, store, "R03")
        new = arrive(session, bid, store, "R04")

        assert old.superseded_by_id == new.id

    def test_a_late_arriving_older_revision_never_becomes_current(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        arrive(session, bid, store, "R04")
        late = arrive(session, bid, store, "R02")

        assert late.state == "superseded"
        assert current_labels(session, bid) == ["R04"]

    def test_the_projects_scheme_decides_the_order(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        """Alphabetically T2 is later than C1. On this project, construction issues follow."""
        project = session.get(Project, bid.project_id)
        assert project is not None
        project.revision_scheme = "tender-then-construction"

        arrive(session, bid, store, "C1")
        arrive(session, bid, store, "T2")

        assert current_labels(session, bid) == ["C1"]

    def test_every_transition_is_audited(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        from firebid.db.models.audit import AuditEvent

        arrive(session, bid, store, "R03")
        arrive(session, bid, store, "R04")

        actions = [
            event.action
            for event in session.execute(
                select(AuditEvent).where(AuditEvent.entity_type == "sheet_revision")
            ).scalars()
        ]
        assert actions.count("sheet revision: supersede") == 1
        assert actions.count("sheet revision: mark current") == 2


class TestConflictingSources:
    def test_disagreeing_sources_land_in_conflict_and_out_of_takeoff(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        """The title block says R04; the filename says R03."""
        revision = arrive(session, bid, store, "R04", filename=f"{NUMBER}-R03.dxf")

        assert revision.state == "conflict"
        assert revision.conflict_reason is not None
        assert "the filename says R03" in revision.conflict_reason
        assert current_labels(session, bid) == [], "a Conflict is never measured"
        task = session.execute(select(HumanTask)).scalar_one()
        assert task.kind == "revision_conflict"

    def test_a_resolved_conflict_returns_to_the_current_set(
        self, session: Session, bid: Bid, store: MemoryObjectStore, person: Actor
    ) -> None:
        revision = arrive(session, bid, store, "R04", filename=f"{NUMBER}-R03.dxf")

        resolve_conflict(
            session,
            revision,
            outcome=State.CURRENT,
            actor=person,
            reason="title block is right; the file was misnamed",
            revision_label="R04",
        )

        assert revision.state == "current"
        assert revision.conflict_reason is None
        assert current_labels(session, bid) == ["R04"]
        assert session.execute(select(HumanTask)).scalar_one().state == "done"

    def test_resolving_as_current_supersedes_the_one_that_was(
        self, session: Session, bid: Bid, store: MemoryObjectStore, person: Actor
    ) -> None:
        arrive(session, bid, store, "R03")
        disputed = arrive(session, bid, store, "R04", filename=f"{NUMBER}-R05.dxf")
        assert current_labels(session, bid) == ["R03"], "the dispute changes nothing yet"

        resolve_conflict(
            session, disputed, outcome=State.CURRENT, actor=person, reason="R04 confirmed"
        )

        assert states(session) == {"R03": "superseded", "R04": "current"}

    def test_the_platform_cannot_resolve_a_conflict(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        from firebid.domain.actors import SYSTEM_ACTOR
        from firebid.domain.state_machines import TransitionError

        revision = arrive(session, bid, store, "R04", filename=f"{NUMBER}-R03.dxf")

        with pytest.raises(TransitionError):
            resolve_conflict(
                session, revision, outcome=State.CURRENT, actor=SYSTEM_ACTOR, reason="guess"
            )


class TestAnOrderThatCannotBeSettled:
    def test_revisions_the_scheme_cannot_order_both_wait_for_a_person(
        self, session: Session, bid: Bid, store: MemoryObjectStore, person: Actor
    ) -> None:
        """X1 and Y1 are in no series of the scheme, and were issued the same day."""
        first = arrive(session, bid, store, "X1")
        second = arrive(session, bid, store, "Y1")

        assert {first.state, second.state} == {"conflict"}
        assert current_labels(session, bid) == []

        resolve_conflict(session, second, outcome=State.CURRENT, actor=person, reason="Y1 later")

        assert states(session) == {"X1": "superseded", "Y1": "current"}


class TestTransmittals:
    def test_a_transmittal_is_read_into_entries_and_filed_as_a_schedule(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        document = receive_transmittal(
            session, bid, store, transmittal((NUMBER, "R04", date(2026, 6, 12)))
        )

        entry = session.execute(select(TransmittalEntry)).scalar_one()
        assert (entry.sheet_number, entry.revision_label) == (NUMBER, "R04")
        assert entry.issued_on == date(2026, 6, 12)
        assert document.doc_type == "schedule"

    def test_a_transmittal_that_agrees_is_recorded_as_a_source(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        receive_transmittal(session, bid, store, transmittal((NUMBER, "R04", None)))

        revision = arrive(session, bid, store, "R04")

        assert revision.state == "current"
        assert revision.sources is not None
        assert revision.sources["transmittal"] == "R04"

    def test_a_transmittal_arriving_later_that_disagrees_pulls_the_drawing_out(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        """Files in one upload are read in any order; the check must not depend on it."""
        revision = arrive(session, bid, store, "R04")
        assert revision.state == "current"

        receive_transmittal(session, bid, store, transmittal((NUMBER, "R05", None)))

        assert revision.state == "conflict"
        assert revision.conflict_reason is not None
        assert "the transmittal says R05" in revision.conflict_reason
        assert current_labels(session, bid) == []


class TestProposalsNotYetIdentified:
    def test_an_unsure_reading_stays_received_and_out_of_takeoff(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        drawing, space = synthetic._new_drawing()
        space.add_text("FIRE SPRINKLER LAYOUT", height=250).set_placement((1_000, 1_000))
        outcome = Ingestor(session, store, AlwaysCleanScanner(), bid_id=bid.id).ingest(
            "untitled.dxf", synthetic.dxf_bytes(drawing)
        )
        document = outcome.stored[0]
        [proposal] = read_title_blocks(
            session, store, document, process_document(session, store, document).sheets
        )

        assert proposal.state == "received"
        assert current_labels(session, bid) == []
