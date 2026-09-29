"""The staged parse pipeline: a drawing set read sheet by sheet, in parallel (ADR-010).

Three kinds of job, each committing its own work, all in the sandbox pool:

* `start` (`parse.document`): check the scan, list the pages, register the sheets, and
  queue one `parse.sheet` for each sheet not yet parsed.
* `parse_sheet` (`parse.sheet`): one sheet from start to finish. The slow work comes first
  and touches nothing another sheet depends on: its low tiles, its geometry, its title block
  reading and crop, and its symbol shapes. Its title block proposal, which other sheets do
  read, is stored under the bid's lock and committed at once. Its views and symbols are its
  own rows and need no lock, except a legend sheet's legend rows. "Parsed" is marked under
  the lock, and the sheet that finds itself the last one queues `finish`.
* `finish` (`parse.finish`): what needs every sheet: match the bid's symbols, detect, queue
  the takeoff, classify the document, and mark it `done`.

Every step is idempotent, because delivery is at-least-once: a parsed sheet is skipped, and
`finish` can run twice and change nothing the second time.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from firebid.db.models.documents import Document, Sheet
from firebid.db.models.drawings import SheetGeometry
from firebid.sandbox.runner import SandboxFailure, run_sandboxed
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

    # 1. The slow work, outside the lock: none of it is read by another sheet.
    payload = store.get(document.storage_key)
    lap("fetch")
    render_sheet(store, document, payload, sheet)
    lap("tiles")
    record: SheetGeometry | None = None
    try:
        record = geometry_service.extract_sheet(session, store, document, sheet, payload)
    except SandboxFailure as failure:
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
    sheet.parse_error = None
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
    """Everything that needs the whole set: symbols matched across the bid, detections, the
    takeoff queued, the document classified and `done`. Idempotent."""
    from firebid.services.classification import classify_in_sandbox
    from firebid.services.detection import detect_all as detect_sheets
    from firebid.services.qto import queue_recompute
    from firebid.services.symbols import consultant_of, match_instances

    sheets = list(
        session.execute(
            select(Sheet).where(Sheet.document_id == document.id).order_by(Sheet.index_in_document)
        ).scalars()
    )
    lock_bid(session, document.bid_id)
    match_instances(session, document.bid_id, consultant_of(session, document.bid_id))
    detected = detect_sheets(session, store, sheets)
    queue_recompute(session, document.bid_id, user_id)
    classify_in_sandbox(session, document, store.get(document.storage_key))
    document.state = "done"
    document.rejected_reason = None
    session.flush()
    failed = sum(1 for sheet in sheets if sheet.parse_error)
    log.info(
        "parse_finished",
        document_id=str(document.id),
        sheets=len(sheets),
        failed_sheets=failed,
        detected=len(detected),
    )
    return {"sheets": len(sheets), "failed_sheets": failed}
