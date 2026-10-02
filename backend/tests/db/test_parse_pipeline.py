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
from firebid.db.models.core import AppUser, Bid
from firebid.db.models.documents import Document, Sheet, SheetRevision
from firebid.db.models.drawings import SheetGeometry
from firebid.evals.synthetic import general_arrangement, write_pdf
from firebid.ingest.scanning import AlwaysCleanScanner
from firebid.jobs.tasks import parse_document, parse_sheet, retry_stalled_parse
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


def upload(session: Session, bid: Bid, store: MemoryObjectStore, payload: bytes) -> Document:
    outcome = Ingestor(session, store, AlwaysCleanScanner(), bid_id=bid.id).ingest(
        "tender set.pdf", payload
    )
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


def test_a_parse_job_whose_worker_stopped_is_queued_again(session: Session) -> None:
    """A worker that dies mid-job leaves it `doing` for ever; the sweep puts it back."""
    job_id = session.execute(
        text(
            "INSERT INTO procrastinate_jobs (queue_name, task_name, args, status) "
            "VALUES ('parse', 'parse.sheet', '{}', 'doing') RETURNING id"
        )
    ).scalar_one()
    session.commit()

    retried = retry_stalled_parse(0)

    session.expire_all()
    status = session.execute(
        text("SELECT status FROM procrastinate_jobs WHERE id = :id"), {"id": job_id}
    ).scalar_one()
    assert retried >= 1 and status == "todo"
    session.execute(text("DELETE FROM procrastinate_jobs WHERE id = :id"), {"id": job_id})
    session.commit()
