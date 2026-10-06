"""The staged parse pipeline (ADR-010): a drawing set read sheet by sheet, each in its own job.

What the old single job guaranteed must still hold, and what it could not do must now:
every sheet gets its title block, geometry and symbols; the document is `done` only when the
whole set has been read and classified; a job delivered twice changes nothing; one sheet that
fails never holds up the rest; and a job whose worker died is queued again.
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path
from typing import Any, cast

import pypdfium2 as pdfium
import pytest
from procrastinate import JobContext
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from firebid.api.progress import read_progress
from firebid.db.models.audit import AuditEvent
from firebid.db.models.core import AppUser, Bid
from firebid.db.models.documents import Document, Sheet, SheetRevision
from firebid.db.models.drawings import SheetGeometry, SheetView
from firebid.domain.actors import Actor
from firebid.drawings import views as view_detection
from firebid.evals.synthetic import dxf_bytes, general_arrangement, write_pdf
from firebid.ingest.scanning import AlwaysCleanScanner
from firebid.jobs.tasks import (
    detect_views_again,
    parse_document,
    parse_sheet,
    retry_stalled_jobs,
)
from firebid.services import parse_pipeline
from firebid.services.ingestion import Ingestor
from firebid.storage import object_store
from firebid.storage.object_store import MemoryObjectStore
from tests.db.jobs import run_parse

pytestmark = pytest.mark.req("NFR-01")
SHEETS = 3


@pytest.fixture(scope="module")
def three_sheet_pdf(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """Three plans in one file, each with its own drawing number."""
    folder = tmp_path_factory.mktemp("set")
    merged = pdfium.PdfDocument.new()
    for index in range(SHEETS):
        drawing, _ = general_arrangement(sheet_number=f"FP-L0{index + 1}-20{index}", columns=4)
        page = pdfium.PdfDocument(str(write_pdf(drawing, Path(folder) / f"{index}.pdf")))
        merged.import_pages(page)
        page.close()
    buffer = io.BytesIO()
    merged.save(buffer)
    return buffer.getvalue()


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> MemoryObjectStore:
    memory = MemoryObjectStore()
    monkeypatch.setattr(object_store, "get_object_store", lambda: memory)
    return memory


def upload(
    session: Session,
    bid: Bid,
    store: MemoryObjectStore,
    payload: bytes,
    name: str = "tender set.pdf",
) -> Document:
    outcome = Ingestor(session, store, AlwaysCleanScanner(), bid_id=bid.id).ingest(name, payload)
    assert outcome.stored, outcome.rejected
    session.commit()
    return outcome.stored[0]


def queued(session: Session, task: str) -> list[dict[str, Any]]:
    session.commit()
    return [
        dict(row)
        for row in session.execute(
            text("SELECT args FROM procrastinate_jobs WHERE task_name = :task AND status = 'todo'"),
            {"task": task},
        ).scalars()
    ]


def sheets_of(session: Session, document: Document) -> list[Sheet]:
    return list(
        session.execute(
            select(Sheet).where(Sheet.document_id == document.id).order_by(Sheet.index_in_document)
        ).scalars()
    )


class TestFanOut:
    def test_the_document_job_registers_the_sheets_and_queues_one_job_each(
        self,
        session: Session,
        bid: Bid,
        user: AppUser,
        store: MemoryObjectStore,
        three_sheet_pdf: bytes,
        as_application_role: None,
    ) -> None:
        document = upload(session, bid, store, three_sheet_pdf)

        parse_document(cast(JobContext, None), document_id=str(document.id), user_id=str(user.id))

        session.expire_all()
        sheets = sheets_of(session, document)
        assert len(sheets) == SHEETS
        mine = {str(sheet.id) for sheet in sheets}
        assert {job["sheet_id"] for job in queued(session, "parse.sheet")} >= mine
        assert session.get(Document, document.id).state == "processing"  # type: ignore[union-attr]
        assert read_progress(session, bid.id).sheets_parsed == 0
        assert not read_progress(session, bid.id).finished

    def test_every_sheet_is_read_and_the_last_one_finishes_the_document(
        self,
        session: Session,
        bid: Bid,
        user: AppUser,
        store: MemoryObjectStore,
        three_sheet_pdf: bytes,
        as_application_role: None,
    ) -> None:
        document = upload(session, bid, store, three_sheet_pdf)

        result = run_parse(session, document.id, user.id)

        tasks = [job["task"] for job in result["jobs"]]
        assert tasks.count("parse.sheet") == SHEETS
        assert tasks.count("parse.finish") == 1, "exactly one sheet finds itself the last"
        assert [job["result"]["last"] for job in result["jobs"][:SHEETS]].count(True) == 1
        # Then each sheet is detected in a job of its own, and the last completes the document.
        assert tasks.count("detection.sheet") == SHEETS
        assert tasks.count("parse.complete") == 1
        assert tasks.index("parse.finish") < tasks.index("detection.sheet")
        assert tasks[-1] == "parse.complete"
        detected = [job["result"] for job in result["jobs"] if job["task"] == "detection.sheet"]
        assert [d["last"] for d in detected].count(True) == 1
        sheets = sheets_of(session, document)
        assert all(sheet.parsed_at is not None and sheet.parse_error is None for sheet in sheets)
        assert all(
            sheet.detected_at is not None and sheet.detection_error is None for sheet in sheets
        )
        for sheet in sheets:
            assert session.execute(
                select(SheetRevision).where(SheetRevision.sheet_id == sheet.id)
            ).scalar_one_or_none(), "each sheet gets its title block proposal"
            assert session.execute(
                select(SheetGeometry).where(SheetGeometry.sheet_id == sheet.id)
            ).scalar_one_or_none(), "and its geometry"
        finished = session.get(Document, document.id)
        assert finished is not None and finished.state == "done" and finished.doc_type
        progress = read_progress(session, bid.id)
        assert progress.finished and progress.sheets_parsed == progress.sheets == SHEETS


class TestIdempotency:
    def test_a_document_delivered_again_reads_nothing_again(
        self,
        session: Session,
        bid: Bid,
        user: AppUser,
        store: MemoryObjectStore,
        three_sheet_pdf: bytes,
        as_application_role: None,
    ) -> None:
        document = upload(session, bid, store, three_sheet_pdf)
        run_parse(session, document.id, user.id)
        revisions = session.execute(
            select(func.count()).select_from(SheetRevision).where(SheetRevision.bid_id == bid.id)
        ).scalar_one()

        again = run_parse(session, document.id, user.id)

        # Nothing is read again; every sheet's detection finds nothing changed.
        assert [job["task"] for job in again["jobs"]] == [
            "parse.finish",
            *["detection.sheet"] * SHEETS,
            "parse.complete",
        ]
        assert all(
            job["result"]["unchanged"] for job in again["jobs"] if job["task"] == "detection.sheet"
        )
        assert session.get(Document, document.id).state == "done"  # type: ignore[union-attr]
        assert (
            session.execute(
                select(func.count())
                .select_from(SheetRevision)
                .where(SheetRevision.bid_id == bid.id)
            ).scalar_one()
            == revisions
        )

    def test_a_sheet_job_delivered_twice_does_nothing_the_second_time(
        self,
        session: Session,
        bid: Bid,
        user: AppUser,
        store: MemoryObjectStore,
        three_sheet_pdf: bytes,
        as_application_role: None,
    ) -> None:
        document = upload(session, bid, store, three_sheet_pdf)
        run_parse(session, document.id, user.id)
        sheet = sheets_of(session, document)[0]

        repeat = parse_sheet(cast(JobContext, None), sheet_id=str(sheet.id), user_id=str(user.id))

        assert repeat["skipped"] is True
        assert not [
            job for job in queued(session, "parse.finish") if job["document_id"] == str(document.id)
        ], "a skipped sheet is not the last one again"


class TestFailure:
    def test_a_sheet_that_fails_is_finished_with_why_and_the_rest_carry_on(
        self,
        session: Session,
        bid: Bid,
        user: AppUser,
        store: MemoryObjectStore,
        three_sheet_pdf: bytes,
        as_application_role: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        document = upload(session, bid, store, three_sheet_pdf)
        parse_document(cast(JobContext, None), document_id=str(document.id), user_id=str(user.id))
        session.expire_all()
        broken = sheets_of(session, document)[1].id
        real = parse_pipeline.parse_sheet

        def failing(session: Session, store: Any, sheet: Sheet, user_id: uuid.UUID) -> Any:
            if sheet.id == broken:
                raise RuntimeError("the page tree is damaged")
            return real(session, store, sheet, user_id)

        monkeypatch.setattr(parse_pipeline, "parse_sheet", failing)
        # The document job has run; run_parse runs it again (idempotent) and then the sheets.
        run_parse(session, document.id, user.id)

        sheets = sheets_of(session, document)
        failed = next(sheet for sheet in sheets if sheet.id == broken)
        assert failed.parsed_at is not None
        assert failed.parse_error and "page tree is damaged" in failed.parse_error
        assert all(sheet.parse_error is None for sheet in sheets if sheet.id != broken)
        assert session.get(Document, document.id).state == "done"  # type: ignore[union-attr]

    def test_a_sheet_whose_linework_could_not_be_read_says_so_and_keeps_its_title_block(
        self,
        session: Session,
        bid: Bid,
        user: AppUser,
        store: MemoryObjectStore,
        three_sheet_pdf: bytes,
        as_application_role: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Found on a real tender: a sheet too large for the sandbox's memory finished as if
        # it had been read, with no linework, no views and nothing to say why.
        from firebid.sandbox.runner import SandboxFailure
        from firebid.services import geometry as geometry_service

        document = upload(session, bid, store, three_sheet_pdf)
        parse_document(cast(JobContext, None), document_id=str(document.id), user_id=str(user.id))
        session.expire_all()
        heavy = sheets_of(session, document)[1].id
        real = geometry_service.extract_sheet

        def too_large(
            session: Session, store: Any, document: Any, sheet: Sheet, payload: Any
        ) -> Any:
            if sheet.id == heavy:
                raise SandboxFailure(
                    "memory", "the file could not be read: Unable to allocate output buffer."
                )
            return real(session, store, document, sheet, payload)

        monkeypatch.setattr(geometry_service, "extract_sheet", too_large)
        run_parse(session, document.id, user.id)

        sheets = sheets_of(session, document)
        unread = next(sheet for sheet in sheets if sheet.id == heavy)
        assert unread.parsed_at is not None
        assert unread.parse_error == (
            "the sheet's linework could not be read: the file could not be read: "
            "Unable to allocate output buffer."
        )
        assert all(sheet.parse_error is None for sheet in sheets if sheet.id != heavy)
        # Its title block was still read: the sheet is in the register, and a person can see
        # which drawing it is that could not be measured.
        read = session.execute(
            select(func.count()).select_from(SheetRevision).where(SheetRevision.sheet_id == heavy)
        ).scalar_one()
        assert read == 1
        stored = session.execute(
            select(func.count()).select_from(SheetGeometry).where(SheetGeometry.sheet_id == heavy)
        ).scalar_one()
        assert stored == 0


class TestDetectionASheet:
    """Detection is a job a sheet, after the document's sheets are all read (ADR-010)."""

    def test_the_document_is_not_done_until_its_last_sheet_is_detected(
        self,
        session: Session,
        bid: Bid,
        user: AppUser,
        store: MemoryObjectStore,
        three_sheet_pdf: bytes,
        as_application_role: None,
    ) -> None:
        from firebid.jobs.tasks import detect_sheet_job, parse_finish

        document = upload(session, bid, store, three_sheet_pdf)
        context = cast(JobContext, None)
        parse_document(context, document_id=str(document.id), user_id=str(user.id))
        session.expire_all()
        sheets = sheets_of(session, document)
        for sheet in sheets:
            parse_sheet(context, sheet_id=str(sheet.id), user_id=str(user.id))

        parse_finish(context, document_id=str(document.id), user_id=str(user.id))

        session.expire_all()
        mine = {str(sheet.id) for sheet in sheets}
        assert {job["sheet_id"] for job in queued(session, "detection.sheet")} >= mine
        assert session.get(Document, document.id).state == "processing"  # type: ignore[union-attr]
        assert not read_progress(session, bid.id).finished
        assert not queued(session, "parse.complete")

        last = [
            detect_sheet_job(context, sheet_id=str(sheet.id), user_id=str(user.id))["last"]
            for sheet in sheets
        ]

        assert last == [False, False, True]
        complete = [
            job
            for job in queued(session, "parse.complete")
            if job["document_id"] == str(document.id)
        ]
        assert len(complete) == 1
        # Still not done: that is the complete job's to say, with the takeoff it queues.
        assert session.get(Document, document.id).state == "processing"  # type: ignore[union-attr]

    def test_a_sheet_whose_detection_fails_is_finished_with_why_and_the_rest_carry_on(
        self,
        session: Session,
        bid: Bid,
        user: AppUser,
        store: MemoryObjectStore,
        three_sheet_pdf: bytes,
        as_application_role: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        document = upload(session, bid, store, three_sheet_pdf)
        parse_document(cast(JobContext, None), document_id=str(document.id), user_id=str(user.id))
        session.expire_all()
        broken = sheets_of(session, document)[1].id
        real = parse_pipeline.detect_sheet

        def failing(
            session: Session, store: Any, sheet: Sheet, user_id: uuid.UUID, **options: Any
        ) -> Any:
            if sheet.id == broken:
                raise RuntimeError("the pipe network could not be traced")
            return real(session, store, sheet, user_id, **options)

        monkeypatch.setattr(parse_pipeline, "detect_sheet", failing)
        run_parse(session, document.id, user.id)

        sheets = sheets_of(session, document)
        failed = next(sheet for sheet in sheets if sheet.id == broken)
        assert failed.detected_at is not None
        assert failed.detection_error and "could not be traced" in failed.detection_error
        assert all(sheet.detection_error is None for sheet in sheets if sheet.id != broken)
        assert session.get(Document, document.id).state == "done"  # type: ignore[union-attr]

    def test_a_detection_job_delivered_twice_does_nothing_the_second_time(
        self,
        session: Session,
        bid: Bid,
        user: AppUser,
        store: MemoryObjectStore,
        three_sheet_pdf: bytes,
        as_application_role: None,
    ) -> None:
        from firebid.jobs.tasks import detect_sheet_job

        document = upload(session, bid, store, three_sheet_pdf)
        run_parse(session, document.id, user.id)
        sheet = sheets_of(session, document)[0]

        repeat = detect_sheet_job(
            cast(JobContext, None), sheet_id=str(sheet.id), user_id=str(user.id)
        )

        assert repeat["skipped"] is True
        assert not [
            job
            for job in queued(session, "parse.complete")
            if job["document_id"] == str(document.id)
        ], "a skipped sheet is not the last one again"

    def test_the_takeoff_is_queued_once_when_the_document_completes(
        self,
        session: Session,
        bid: Bid,
        user: AppUser,
        store: MemoryObjectStore,
        three_sheet_pdf: bytes,
        as_application_role: None,
    ) -> None:
        document = upload(session, bid, store, three_sheet_pdf)

        run_parse(session, document.id, user.id)

        takeoff = [job for job in queued(session, "qto.recompute") if job["bid_id"] == str(bid.id)]
        assert len(takeoff) == 1


@pytest.mark.req("FR-VIS-05")
def test_a_sheet_read_later_proves_the_scale_of_one_read_before_and_it_is_detected_again(
    session: Session, bid: Bid, user: AppUser, store: MemoryObjectStore, as_application_role: None
) -> None:
    # Found on a real tender of one-sheet files: the upper floors carry no dimension and are
    # read before, or after, the floors whose dimensions prove the scale of the grid they share.
    above, _ = general_arrangement(sheet_number="FP-L06-201", with_dimensions=False, columns=4)
    below, _ = general_arrangement(sheet_number="FP-L05-201", columns=4)
    first = upload(session, bid, store, dxf_bytes(above), "FP-L06-201.dxf")
    run_parse(session, first.id, user.id)
    (sheet,) = sheets_of(session, first)

    def view() -> SheetView:
        session.expire_all()
        return session.execute(select(SheetView).where(SheetView.sheet_id == sheet.id)).scalar_one()

    assert view().scale_status == "unverified"
    assert sheet.detected_at is not None

    second = upload(session, bid, store, dxf_bytes(below), "FP-L05-201.dxf")
    run_parse(session, second.id, user.id)

    assert (view().scale_status, view().denominator) == ("verified", 100.0)
    # Its lengths may be measured now, so it is queued to be detected again.
    session.refresh(sheet)
    assert sheet.detected_at is None
    waiting = [job["sheet_id"] for job in queued(session, "detection.sheet")]
    assert waiting.count(str(sheet.id)) == 1
    assert all(
        document.state == "done"
        for document in session.execute(select(Document).where(Document.bid_id == bid.id)).scalars()
    )


@pytest.mark.parametrize(
    ("queue", "task"), [("parse", "parse.sheet"), ("default", "symbol.propose")]
)
def test_a_job_whose_worker_stopped_is_queued_again(
    session: Session, queue: str, task: str
) -> None:
    """A worker that dies mid-job leaves it `doing` for ever; the sweep puts it back,
    whichever queue it was on."""
    job_id = session.execute(
        text(
            "INSERT INTO procrastinate_jobs (queue_name, task_name, args, status) "
            "VALUES (:queue, :task, '{}', 'doing') RETURNING id"
        ),
        {"queue": queue, "task": task},
    ).scalar_one()
    session.commit()

    retried = retry_stalled_jobs(0)

    session.expire_all()
    status, attempts = session.execute(
        text("SELECT status, attempts FROM procrastinate_jobs WHERE id = :id"), {"id": job_id}
    ).one()
    assert retried >= 1 and (status, attempts) == ("todo", 1)
    session.execute(text("DELETE FROM procrastinate_jobs WHERE id = :id"), {"id": job_id})
    session.commit()


def test_a_job_that_keeps_stalling_is_failed_not_queued_for_ever(session: Session) -> None:
    from firebid.jobs.tasks import STALLED_ATTEMPTS

    job_id = session.execute(
        text(
            "INSERT INTO procrastinate_jobs (queue_name, task_name, args, status, attempts) "
            "VALUES ('default', 'qto.recompute', '{}', 'doing', :attempts) RETURNING id"
        ),
        {"attempts": STALLED_ATTEMPTS - 1},
    ).scalar_one()
    session.commit()

    retry_stalled_jobs(0)

    session.expire_all()
    status = session.execute(
        text("SELECT status FROM procrastinate_jobs WHERE id = :id"), {"id": job_id}
    ).scalar_one()
    assert status == "failed"
    session.execute(text("DELETE FROM procrastinate_jobs WHERE id = :id"), {"id": job_id})
    session.commit()


def test_a_job_whose_worker_is_alive_is_left_running(session: Session) -> None:
    worker_id = session.execute(
        text("INSERT INTO procrastinate_workers (last_heartbeat) VALUES (now()) RETURNING id")
    ).scalar_one()
    job_id = session.execute(
        text(
            "INSERT INTO procrastinate_jobs (queue_name, task_name, args, status, worker_id) "
            "VALUES ('default', 'qto.recompute', '{}', 'doing', :worker) RETURNING id"
        ),
        {"worker": worker_id},
    ).scalar_one()
    session.commit()

    retry_stalled_jobs(0)

    session.expire_all()
    status = session.execute(
        text("SELECT status FROM procrastinate_jobs WHERE id = :id"), {"id": job_id}
    ).scalar_one()
    assert status == "doing"
    session.execute(text("DELETE FROM procrastinate_jobs WHERE id = :id"), {"id": job_id})
    session.execute(text("DELETE FROM procrastinate_workers WHERE id = :id"), {"id": worker_id})
    session.commit()


def person(user: AppUser) -> Actor:
    return Actor(label=user.display_name, roles=frozenset({"estimator"}), id=user.id)


@pytest.mark.req("FR-DOC-01")
class TestReadAgain:
    """Found on a real tender: four sheets failed for memory and a drawing was refused when
    it was finished. After the fix nothing in the product could read them again, and views
    found by an older detector stayed as they were; each took a script on the database."""

    def unread(
        self,
        session: Session,
        bid: Bid,
        user: AppUser,
        store: MemoryObjectStore,
        payload: bytes,
        monkeypatch: pytest.MonkeyPatch,
    ) -> tuple[Document, uuid.UUID]:
        """A drawing set whose second sheet's linework could not be read, as it finished."""
        from firebid.sandbox.runner import SandboxFailure
        from firebid.services import geometry as geometry_service

        document = upload(session, bid, store, payload)
        parse_document(cast(JobContext, None), document_id=str(document.id), user_id=str(user.id))
        session.expire_all()
        heavy = sheets_of(session, document)[1].id
        real = geometry_service.extract_sheet

        def too_large(
            session: Session, store: Any, document: Any, sheet: Sheet, payload: Any
        ) -> Any:
            if sheet.id == heavy:
                raise SandboxFailure("memory", "the file needed more memory than a job is given")
            return real(session, store, document, sheet, payload)

        with monkeypatch.context() as patched:
            patched.setattr(geometry_service, "extract_sheet", too_large)
            run_parse(session, document.id, user.id)
        return document, heavy

    def test_a_sheet_that_could_not_be_read_is_said_and_can_be_read_again(
        self,
        session: Session,
        bid: Bid,
        user: AppUser,
        store: MemoryObjectStore,
        three_sheet_pdf: bytes,
        as_application_role: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        document, heavy = self.unread(session, bid, user, store, three_sheet_pdf, monkeypatch)

        # The drawing is "done" with a sheet in it unread: the page says which, and why.
        progress = read_progress(session, bid.id)
        assert progress.finished and progress.failures == []
        (unread,) = progress.unread_sheets
        assert (unread.id, unread.filename, unread.page) == (heavy, "tender set.pdf", 2)
        assert "more memory" in unread.reason
        assert progress.read_again.model_dump() == {"documents": 1, "sheets": 1, "views": 0}

        counts = parse_pipeline.read_again(session, bid.id, person(user))
        session.commit()

        assert counts == {"documents": 1, "sheets": 1, "views": 0}
        sheet = session.get(Sheet, heavy)
        assert sheet is not None and sheet.parsed_at is None and sheet.parse_error is None
        assert session.get(Document, document.id).state == "received"  # type: ignore[union-attr]
        assert [job["document_id"] for job in queued(session, "parse.document")] == [
            str(document.id)
        ]
        event = session.execute(
            select(AuditEvent).where(AuditEvent.action == "documents: read again")
        ).scalar_one()
        assert event.after == counts and event.actor_label == "Esther Tan"
        waiting = read_progress(session, bid.id)
        assert not waiting.finished and waiting.unread_sheets == []
        # Asked again while it waits: nothing more is found, and nothing more is queued.
        assert parse_pipeline.read_again(session, bid.id, person(user)) == {
            "documents": 0,
            "sheets": 0,
            "views": 0,
        }
        assert len(queued(session, "parse.document")) == 1

        session.execute(text("DELETE FROM procrastinate_jobs WHERE task_name = 'parse.document'"))
        session.commit()
        result = run_parse(session, document.id, user.id)

        tasks = [job["task"] for job in result["jobs"]]
        assert tasks.count("parse.sheet") == 1, "only the sheet that failed is read"
        assert tasks.count("parse.finish") == 1 and tasks[-1] == "parse.complete"
        sheets = sheets_of(session, document)
        assert all(sheet.parsed_at is not None and sheet.parse_error is None for sheet in sheets)
        assert session.execute(
            select(SheetGeometry).where(SheetGeometry.sheet_id == heavy)
        ).scalar_one_or_none(), "its linework is read now"
        after = read_progress(session, bid.id)
        assert after.finished and after.counts["done"] == 1
        assert after.read_again.model_dump() == {"documents": 0, "sheets": 0, "views": 0}

    def test_a_drawing_refused_when_it_was_finished_is_read_again_and_one_never_scanned_is_not(
        self,
        session: Session,
        bid: Bid,
        user: AppUser,
        store: MemoryObjectStore,
        three_sheet_pdf: bytes,
        as_application_role: None,
    ) -> None:
        document = upload(session, bid, store, three_sheet_pdf)
        run_parse(session, document.id, user.id)
        refused = session.get(Document, document.id)
        assert refused is not None and refused.scanned_at is not None
        refused.state = "rejected"
        refused.rejected_reason = "the drawing was read but could not be finished: InternalError"
        # Refused when it was sent: its type is not accepted, and it was never scanned.
        unaccepted = Ingestor(session, store, AlwaysCleanScanner(), bid_id=bid.id).ingest(
            "setup.exe", b"MZ" + b"\x00" * 64
        )
        assert unaccepted.rejected and not unaccepted.stored
        session.commit()
        assert read_progress(session, bid.id).counts["rejected"] == 2

        counts = parse_pipeline.read_again(session, bid.id, person(user))
        session.commit()

        assert counts == {"documents": 1, "sheets": 0, "views": 0}
        session.execute(text("DELETE FROM procrastinate_jobs WHERE task_name = 'parse.document'"))
        session.commit()
        result = run_parse(session, document.id, user.id)

        tasks = [job["task"] for job in result["jobs"]]
        assert tasks.count("parse.sheet") == 0, "its sheets were read; only the finish is owed"
        assert tasks.count("parse.finish") == 1
        finished = session.get(Document, document.id)
        assert finished is not None
        assert (finished.state, finished.rejected_reason) == ("done", None)
        progress = read_progress(session, bid.id)
        assert progress.counts["rejected"] == 1 and progress.read_again.documents == 0

    def test_a_drawing_still_being_read_is_left_to_its_jobs(
        self,
        session: Session,
        bid: Bid,
        user: AppUser,
        store: MemoryObjectStore,
        three_sheet_pdf: bytes,
        as_application_role: None,
    ) -> None:
        document = upload(session, bid, store, three_sheet_pdf)
        parse_document(cast(JobContext, None), document_id=str(document.id), user_id=str(user.id))
        session.expire_all()
        parse_pipeline.mark_failed(session, sheets_of(session, document)[0].id, user.id, "boom")
        session.commit()
        assert session.get(Document, document.id).state == "processing"  # type: ignore[union-attr]

        assert parse_pipeline.read_again(session, bid.id, person(user)) == {
            "documents": 0,
            "sheets": 0,
            "views": 0,
        }

    def test_views_found_by_an_older_detector_are_found_again_by_one_job(
        self,
        session: Session,
        bid: Bid,
        user: AppUser,
        store: MemoryObjectStore,
        three_sheet_pdf: bytes,
        as_application_role: None,
    ) -> None:
        document = upload(session, bid, store, three_sheet_pdf)
        run_parse(session, document.id, user.id)
        assert read_progress(session, bid.id).read_again.views == 0
        session.execute(text("UPDATE sheet_view SET detector_version = 'older', grid_marks = NULL"))
        session.commit()
        assert read_progress(session, bid.id).read_again.model_dump() == {
            "documents": 0,
            "sheets": 0,
            "views": SHEETS,
        }

        first = parse_pipeline.read_again(session, bid.id, person(user))
        second = parse_pipeline.read_again(session, bid.id, person(user))
        session.commit()

        assert first == second == {"documents": 0, "sheets": 0, "views": SHEETS}
        assert queued(session, "views.again") == [
            {"bid_id": str(bid.id), "user_id": str(user.id)}
        ], "one job for the bid, however often it is asked"
        assert session.get(Document, document.id).state == "done"  # type: ignore[union-attr]

        result = detect_views_again(
            cast(JobContext, None), bid_id=str(bid.id), user_id=str(user.id)
        )

        assert (result["sheets"], result["failed"]) == (SHEETS, 0)
        session.expire_all()
        versions = set(
            session.execute(
                select(SheetView.detector_version).where(SheetView.bid_id == bid.id)
            ).scalars()
        )
        assert versions == {view_detection.DETECTOR_VERSION}
        assert read_progress(session, bid.id).read_again.views == 0
        # Delivered twice: every sheet's views are this detector's, so none is found again.
        again = detect_views_again(cast(JobContext, None), bid_id=str(bid.id), user_id=str(user.id))
        assert again["sheets"] == 0
