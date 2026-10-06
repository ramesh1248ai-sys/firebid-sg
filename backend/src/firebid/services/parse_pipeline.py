"""The staged parse pipeline: a drawing set read sheet by sheet, in parallel (ADR-010).

Five kinds of job, each committing its own work, all in the sandbox pool:

* `start` (`parse.document`): check the scan, list the pages, register the sheets, and
  queue one `parse.sheet` for each sheet not yet parsed.
* `parse_sheet` (`parse.sheet`): one sheet from start to finish. The slow work comes first
  and touches nothing another sheet depends on: its low tiles, its geometry, its title block
  reading and crop, and its symbol shapes. Its title block proposal, which other sheets do
  read, is stored under the bid's lock and committed at once. Its views and symbols are its
  own rows and need no lock, except a legend sheet's legend rows. "Parsed" is marked under
  the lock, and the sheet that finds itself the last one queues `finish`.
* `finish` (`parse.finish`): what needs every sheet read: match the bid's symbols, classify
  the document, and queue one `detection.sheet` for each of its sheets.
* `detect_sheet` (`detection.sheet`): one sheet's detections. Sheets are detected side by
  side, and a sheet whose inputs are what they were is left as it is (its fingerprint).
  "Detected" is marked under the bid's lock, and the sheet that finds itself the last one
  queues `complete`.
* `complete` (`parse.complete`): queue the takeoff and mark the document `done`.

Detection was one step of `finish`, for every sheet in turn, in one transaction under the
bid's lock: 189 to 237 s on a real tender of 121 sheets, on one process, with nothing to
show until it ended. As a job a sheet it runs on as many processes as the pool has.

Every step is idempotent, because delivery is at-least-once: a parsed sheet is skipped, a
detected sheet is skipped, and `finish` and `complete` can run twice and change nothing
that matters the second time.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from firebid.db.models.documents import Document, Sheet
from firebid.db.models.drawings import SheetGeometry
from firebid.sandbox.runner import SandboxFailure, run_sandboxed
from firebid.services.pages import sheet_payload
from firebid.storage.object_store import ObjectStore

log = structlog.get_logger("firebid.parse_pipeline")


def lock_bid(session: Session, bid_id: uuid.UUID) -> None:
    """Hold the bid's lock until this transaction ends: one sheet's cross-sheet writes at a
    time, so two sheets never both register the same revision or both think they are last."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('firebid.parse:' || :bid))"),
        {"bid": str(bid_id)},
    )


# --- parse.document --------------------------------------------------------------------------


@dataclass
class Started:
    sheets: int = 0
    queued: list[uuid.UUID] = field(default_factory=list)
    failure: str | None = None
    finished: bool = False  # every sheet was already parsed: `finish` was queued instead


def start(session: Session, store: ObjectStore, document: Document, user_id: uuid.UUID) -> Started:
    """Register a drawing's sheets and queue each unparsed one. Queues the jobs in the same
    transaction, so a rollback leaves none behind."""
    from firebid.jobs.enqueue import enqueue
    from firebid.jobs.tasks import parse_sheet as parse_sheet_task
    from firebid.services import object_library
    from firebid.services.sheets import process_document
    from firebid.services.symbols import consultant_of

    outcome = process_document(session, store, document, staged=True)
    started = Started(sheets=len(outcome.sheets), failure=outcome.failure)
    if outcome.failure or not outcome.sheets:
        return started
    # Seeded once here, not by every sheet at once.
    object_library.ensure_seeded(session, consultant_of(session, document.bid_id).organisation_id)
    for sheet in outcome.sheets:
        if sheet.parsed_at is None:
            enqueue(session, parse_sheet_task, sheet_id=str(sheet.id), user_id=str(user_id))
            started.queued.append(sheet.id)
    if not started.queued:
        queue_finish(session, document.id, user_id)
        started.finished = True
    log.info(
        "parse_started",
        document_id=str(document.id),
        sheets=started.sheets,
        queued=len(started.queued),
    )
    return started


# --- parse.sheet -----------------------------------------------------------------------------


@dataclass
class SheetParsed:
    sheet_id: uuid.UUID
    skipped: bool = False
    last: bool = False
    seconds: dict[str, float] = field(default_factory=dict)


def parse_sheet(
    session: Session, store: ObjectStore, sheet: Sheet, user_id: uuid.UUID
) -> SheetParsed:
    """Everything one sheet needs, then "parsed". Skips a sheet already parsed."""
    import time

    from firebid.jobs.enqueue import enqueue
    from firebid.jobs.tasks import propose_symbol
    from firebid.services import geometry as geometry_service
    from firebid.services import symbols as symbol_service
    from firebid.services import title_blocks, views
    from firebid.services.sheets import render_sheet

    result = SheetParsed(sheet.id)
    if sheet.parsed_at is not None:
        result.skipped = True
        return result
    document = session.get(Document, sheet.document_id)
    if document is None:
        raise ValueError("the sheet's document is gone")

    clock = time.perf_counter()

    def lap(stage: str) -> None:
        nonlocal clock
        now = time.perf_counter()
        result.seconds[stage] = round(now - clock, 3)
        clock = now

    # 1. The slow work, outside the lock: none of it is read by another sheet. The sheet's
    #    own page where the document was cut into pages, not the whole set again.
    payload = sheet_payload(store, document, sheet)
    lap("fetch")
    render_sheet(store, document, payload, sheet)
    lap("tiles")
    record: SheetGeometry | None = None
    unread: str | None = None
    try:
        record = geometry_service.extract_sheet(session, store, document, sheet, payload)
    except SandboxFailure as failure:
        # The sheet is still registered and its title block read, but nothing can be
        # measured on it: that is a failed sheet, and it says so (found on a real tender,
        # where a sheet too large for the sandbox finished as if it had been read).
        unread = f"the sheet's linework could not be read: {failure.reason}"
        log.warning("geometry_failed", sheet_id=str(sheet.id), reason=failure.reason)
    lap("geometry")
    reading = title_blocks.reading_ahead(session, store, document, sheet, payload)
    lap("title_block_reading")
    shapes = _shapes(store, record) if record is not None else None
    lap("symbol_shapes")

    # 2. The title block proposal, under the bid's lock: renditions and the current revision
    #    of a drawing number are shared between sheets. Committed at once, which releases the
    #    lock; a retry after this point finds the proposal and reads it back.
    lock_bid(session, sheet.bid_id)
    lap("lock_wait")
    title_blocks.read_one(session, store, document, sheet, payload, found=reading)
    session.commit()
    lap("title_block_store")

    # 3. The sheet's own rows, with no lock: its views, then its symbols. Only legend rows
    #    propose mappings shared across the organisation, so only a legend sheet waits.
    if record is not None:
        views.detect_sheet(session, store, record)
        lap("views")
        if shapes is None or shapes[0]:
            lock_bid(session, sheet.bid_id)
        consultant = symbol_service.consultant_of(session, sheet.bid_id)
        read = symbol_service.read_sheet(
            session, store, record, consultant, match=False, shapes=shapes
        )
        for entry_id in read.awaiting_model:
            enqueue(session, propose_symbol, entry_id=str(entry_id), user_id=str(user_id))
        lap("symbols_record")

    # 4. "Parsed", and whether it was the last, under the lock again: held only to the commit.
    lock_bid(session, sheet.bid_id)
    sheet.parsed_at = datetime.now(UTC)
    sheet.parse_error = unread[:2000] if unread else None
    session.flush()
    result.last = _queue_finish_if_last(session, sheet.document_id, user_id)
    log.info(
        "sheet_parsed",
        sheet_id=str(sheet.id),
        last=result.last,
        # Flat, one field a stage: the log redactor hides nested values.
        **{f"{stage}_s": seconds for stage, seconds in result.seconds.items()},
    )
    return result


def _shapes(store: ObjectStore, record: SheetGeometry) -> Any:
    """The sheet's legends and symbols, found in their own process.

    The heaviest pure work on a sheet. Jobs run as threads of the worker, so work done in the
    worker's own process would take turns on one interpreter; a process of its own runs side
    by side with the other sheets'. None when it fails: recording finds them itself then.
    """
    from firebid.services.symbols import sheet_shapes

    page = (record.page[0], record.page[1], record.page[2], record.page[3])
    try:
        return run_sandboxed(sheet_shapes, store.get(record.object_key), page)
    except SandboxFailure as failure:
        log.warning("symbol_shapes_failed", sheet_id=str(record.sheet_id), reason=failure.reason)
        return None


def mark_failed(session: Session, sheet_id: uuid.UUID, user_id: uuid.UUID, reason: str) -> bool:
    """A sheet whose job failed is finished too, with why, so its document can finish.
    Returns whether it was the last."""
    sheet = session.get(Sheet, sheet_id)
    if sheet is None or sheet.parsed_at is not None:
        return False
    lock_bid(session, sheet.bid_id)
    sheet.parsed_at = datetime.now(UTC)
    sheet.parse_error = reason[:2000]
    session.flush()
    log.warning("sheet_parse_failed", sheet_id=str(sheet_id), reason=reason[:300])
    return _queue_finish_if_last(session, sheet.document_id, user_id)


def _queue_finish_if_last(session: Session, document_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """Under the bid's lock, so exactly one sheet sees none left."""
    left = session.execute(
        select(func.count())
        .select_from(Sheet)
        .where(Sheet.document_id == document_id, Sheet.parsed_at.is_(None))
    ).scalar_one()
    if left:
        return False
    queue_finish(session, document_id, user_id)
    return True


def queue_finish(session: Session, document_id: uuid.UUID, user_id: uuid.UUID) -> None:
    from firebid.jobs.enqueue import enqueue_once
    from firebid.jobs.tasks import parse_finish

    enqueue_once(
        session,
        parse_finish,
        f"parse.finish:{document_id}",
        document_id=str(document_id),
        user_id=str(user_id),
    )


# --- parse.finish ----------------------------------------------------------------------------


def finish(
    session: Session, store: ObjectStore, document: Document, user_id: uuid.UUID
) -> dict[str, int]:
    """What needs every sheet read: symbols matched across the bid, the document classified,
    and each sheet queued to be detected. The document stays `processing` until the last of
    them is. Idempotent."""
    from firebid.jobs.enqueue import enqueue
    from firebid.jobs.tasks import detect_sheet_job
    from firebid.services import views
    from firebid.services.classification import classify_in_sandbox
    from firebid.services.symbols import consultant_of, match_instances

    sheets = list(
        session.execute(
            select(Sheet).where(Sheet.document_id == document.id).order_by(Sheet.index_in_document)
        ).scalars()
    )
    lock_bid(session, document.bid_id)
    # A sheet that only states its scale is checked against the grid of the sheets whose
    # scale is proved, whichever was read first: so across the bid, each time one finishes.
    regraded = views.check_against_grid(session, document.bid_id)
    match_instances(session, document.bid_id, consultant_of(session, document.bid_id))
    classify_in_sandbox(session, document, store.get(document.storage_key))
    for sheet in sheets:
        sheet.detected_at = None
        sheet.detection_error = None
    session.flush()
    for sheet in sheets:
        enqueue(session, detect_sheet_job, sheet_id=str(sheet.id), user_id=str(user_id))
    if not sheets:
        queue_complete(session, document.id, user_id)
    if {view.sheet_id for view in regraded} - {sheet.id for sheet in sheets}:
        # Another document's sheet may be measured now, or may no longer be: its lengths
        # are detected again. Found by fingerprint, which a view's scale is part of.
        detect_again(session, document.bid_id, user_id)
    failed = sum(1 for sheet in sheets if sheet.parse_error)
    log.info(
        "parse_finished",
        document_id=str(document.id),
        sheets=len(sheets),
        failed_sheets=failed,
        queued_for_detection=len(sheets),
    )
    return {"sheets": len(sheets), "failed_sheets": failed}


# --- detection.sheet -------------------------------------------------------------------------


@dataclass
class SheetDetected:
    sheet_id: uuid.UUID
    skipped: bool = False
    last: bool = False
    unchanged: bool = False
    objects: int = 0
    runs: int = 0


def detect_sheet(
    session: Session,
    store: ObjectStore,
    sheet: Sheet,
    user_id: uuid.UUID | None,
    *,
    force: bool = False,
) -> SheetDetected:
    """One sheet's detections, then "detected".

    A sheet with no geometry (its reading failed) has nothing to detect and is finished at
    once. The work is done without the bid's lock: a sheet's detections are its own rows.

    A sheet already marked detected is looked at all the same: a job queued for an earlier
    decision may have marked it while a later decision was being made. Its fingerprint says
    whether anything is left to do; when nothing is, the job is skipped and finishes nothing.
    """
    from firebid.services import detection

    result = SheetDetected(sheet.id)
    already = sheet.detected_at is not None
    record = session.execute(
        select(SheetGeometry).where(SheetGeometry.sheet_id == sheet.id)
    ).scalar_one_or_none()
    if record is not None:
        outcome = detection.detect_sheet(session, store, record, force=force and not already)
        result.unchanged = outcome.unchanged
        result.objects, result.runs = outcome.objects, outcome.runs
    if already and (record is None or result.unchanged):
        result.skipped = True
        return result

    lock_bid(session, sheet.bid_id)
    sheet.detected_at = datetime.now(UTC)
    sheet.detection_error = None
    session.flush()
    result.last = _queue_complete_if_last(session, sheet.document_id, user_id)
    return result


def mark_detection_failed(
    session: Session, sheet_id: uuid.UUID, user_id: uuid.UUID | None, reason: str
) -> bool:
    """A sheet whose detection failed is finished too, with why, so its document can finish.
    Returns whether it was the last."""
    sheet = session.get(Sheet, sheet_id)
    if sheet is None or sheet.detected_at is not None:
        return False
    lock_bid(session, sheet.bid_id)
    sheet.detected_at = datetime.now(UTC)
    sheet.detection_error = reason[:2000]
    session.flush()
    log.warning("sheet_detection_failed", sheet_id=str(sheet_id), reason=reason[:300])
    return _queue_complete_if_last(session, sheet.document_id, user_id)


def _queue_complete_if_last(
    session: Session, document_id: uuid.UUID, user_id: uuid.UUID | None
) -> bool:
    """Under the bid's lock, so exactly one sheet sees none left."""
    left = session.execute(
        select(func.count())
        .select_from(Sheet)
        .where(Sheet.document_id == document_id, Sheet.detected_at.is_(None))
    ).scalar_one()
    if left:
        return False
    queue_complete(session, document_id, user_id)
    return True


def queue_complete(session: Session, document_id: uuid.UUID, user_id: uuid.UUID | None) -> None:
    from firebid.jobs.enqueue import enqueue_once
    from firebid.jobs.tasks import parse_complete

    enqueue_once(
        session,
        parse_complete,
        f"parse.complete:{document_id}",
        document_id=str(document_id),
        user_id=str(user_id) if user_id else "",
    )


# --- detection.run ---------------------------------------------------------------------------


def detect_again(
    session: Session, bid_id: uuid.UUID, user_id: uuid.UUID | None, *, force: bool = False
) -> dict[str, int]:
    """After a mapping decision: queue a `detection.sheet` for each sheet it reaches.

    Which sheets those are is found by fingerprint, so a decision about one symbol queues
    the sheets that draw it and no others; `force` queues every sheet (a person asked).
    Each sheet's document completes again when its sheets are detected, which queues the
    takeoff. With nothing to detect the takeoff is queued here: a decision can change what
    an item is without changing what is detected.

    It was one job that detected every sheet in turn, on one process: 157 s on a real
    tender. The sheets are detected side by side now, in the parser pool.
    """
    from firebid.jobs.enqueue import enqueue_once
    from firebid.jobs.tasks import detect_sheet_job
    from firebid.services import detection
    from firebid.services.qto import queue_recompute

    lock_bid(session, bid_id)
    if force:
        records = list(
            session.execute(select(SheetGeometry).where(SheetGeometry.bid_id == bid_id)).scalars()
        )
    else:
        records = detection.out_of_date(session, bid_id)
    sheets = list(
        session.execute(
            select(Sheet).where(Sheet.id.in_([record.sheet_id for record in records]))
        ).scalars()
    )
    for sheet in sheets:
        sheet.detected_at = None
        sheet.detection_error = None
    session.flush()
    if sheets:
        # A finished document's other sheets are detected already, whether or not they say
        # so (a document read before detection was a job a sheet never marked them): only
        # the sheets queued here stand between it and completing again.
        session.execute(
            update(Sheet)
            .where(
                Sheet.document_id.in_({sheet.document_id for sheet in sheets}),
                Sheet.id.not_in([sheet.id for sheet in sheets]),
                Sheet.detected_at.is_(None),
                Sheet.document_id.in_(select(Document.id).where(Document.state == "done")),
            )
            .values(detected_at=func.now())
            .execution_options(synchronize_session=False)
        )
    # One waiting job a sheet: a second decision made before the first one's jobs have run
    # finds them waiting, and they read the mappings as they are when they run.
    queued = 0
    for sheet in sheets:
        job = enqueue_once(
            session,
            detect_sheet_job,
            f"detection.sheet:{sheet.id}:{'forced' if force else 'changed'}",
            sheet_id=str(sheet.id),
            user_id=str(user_id) if user_id else "",
            force=force,
        )
        queued += job is not None
    if not sheets:
        queue_recompute(session, bid_id, user_id)
    log.info("detection_queued", bid_id=str(bid_id), sheets=len(sheets), queued=queued, force=force)
    return {"sheets": len(sheets)}


# --- parse.complete --------------------------------------------------------------------------


def complete(session: Session, document: Document, user_id: uuid.UUID | None) -> dict[str, int]:
    """Every sheet is read and detected: queue the takeoff and mark the document `done`.

    Also reached when a document's sheets are detected again after a mapping decision: a
    document that was refused stays refused.
    """
    from firebid.services.qto import queue_recompute

    queue_recompute(session, document.bid_id, user_id)
    if document.state in ("processing", "done"):
        document.state = "done"
        document.rejected_reason = None
    session.flush()
    failed = session.execute(
        select(func.count())
        .select_from(Sheet)
        .where(Sheet.document_id == document.id, Sheet.detection_error.is_not(None))
    ).scalar_one()
    log.info("parse_completed", document_id=str(document.id), failed_detections=int(failed))
    return {"failed_detections": int(failed)}
