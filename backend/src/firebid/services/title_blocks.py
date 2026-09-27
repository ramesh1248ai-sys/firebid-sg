"""Reading each sheet's title block into a sheet revision proposal (FR-DOC-02).

Two halves, split by where they may run:

* `read_sheet` runs in the **sandbox pool**, beside the parse job, because it opens the
  tender file. It tries, in order: the page's own text (a PDF text layer or DXF entities),
  OCR of the corners a title block sits in, and a layout remembered for this consultant. The
  best reading becomes a `SheetRevision` in state `received`: a proposal, with how each field
  was read recorded beside it.
* `check_with_model` runs on the **ordinary worker**, because it calls a model and the pool
  has no network. It is queued only when the reading is not confident enough, and it is sent
  a PNG crop the pool rendered and the text spans, never the file.

Whatever either half produces is a proposal (guardrail 2). Registering it, and ordering it
against other revisions, is the revision service's job.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from dataclasses import dataclass
from typing import Any, cast

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.models.core import Bid
from firebid.db.models.documents import Document, Sheet, SheetRevision, TitleBlockLayout
from firebid.drawings.title_block import (
    CANDIDATES,
    CRITICAL,
    DEFAULT_THRESHOLD,
    Box,
    Field,
    FieldReading,
    Layout,
    Span,
    TitleBlockReading,
    fingerprint,
    parse_date,
    read,
    read_with_layout,
    similarity,
)
from firebid.sandbox.runner import SandboxFailure, run_sandboxed
from firebid.storage.object_store import ObjectExists, ObjectStore

log = structlog.get_logger("firebid.title_blocks")

# A remembered layout is used when the page's labels sit where it expects this much of the time.
LAYOUT_MATCH = 0.8

# How each reading was obtained, most trustworthy source first.
METHOD_TEXT_LAYER = "text_layer"
METHOD_CAD = "cad"
METHOD_OCR = "ocr"
METHOD_LAYOUT = "layout"
METHOD_MODEL = "model"
METHOD_PERSON = "person"


@dataclass
class SheetReading:
    """A reading and where it came from, before it is stored."""

    reading: TitleBlockReading
    method: str
    spans: list[Span]
    page: Box
    ocr_confidence: float | None = None


def read_title_blocks(
    session: Session, store: ObjectStore, document: Document, sheets: list[Sheet]
) -> list[SheetRevision]:
    """Read every sheet of one document. One unreadable sheet never stops the others.

    A sheet whose reading fails outright still gets a proposal, with nothing read and a review
    task, so it appears in the register as something a person must identify rather than
    silently missing from it.
    """
    payload = store.get(document.storage_key)
    revisions = []
    for sheet in sheets:
        try:
            revisions.append(read_sheet(session, store, document, sheet, payload))
        except SandboxFailure as failure:
            log.warning("title_block_unreadable", sheet_id=str(sheet.id), reason=failure.reason)
            revision = SheetRevision(
                bid_id=document.bid_id,
                sheet_id=sheet.id,
                reading={"method": "none", "fields": {}, "error": failure.reason},
                created_by_id=document.created_by_id,
            )
            session.add(revision)
            session.flush()
            raise_review(session, revision, f"the title block could not be read: {failure.reason}")
            revisions.append(revision)
    return revisions


def read_sheet(
    session: Session, store: ObjectStore, document: Document, sheet: Sheet, payload: bytes
) -> SheetRevision:
    """Read one sheet's title block and store it as a proposal. Idempotent per sheet."""
    existing = session.execute(
        select(SheetRevision).where(SheetRevision.sheet_id == sheet.id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    found = _best_reading(session, document, sheet, payload)
    if found.ocr_confidence is not None:
        sheet.quality_detail = {
            **(sheet.quality_detail or {}),
            "ocr_confidence": round(found.ocr_confidence, 4),
        }

    revision = _store(session, document, sheet, found)
    if revision.sheet_id != sheet.id:
        return revision  # another rendition of a revision already on the bid

    if found.reading.needs_help(DEFAULT_THRESHOLD):
        _ask_for_help(session, store, document, sheet, payload, revision, found)
    else:
        from firebid.services.revisions import settle

        settle(session, revision)
    session.flush()
    return revision


def _best_reading(
    session: Session, document: Document, sheet: Sheet, payload: bytes
) -> SheetReading:
    from firebid.parsing import text as text_parsing

    if document.kind == "dxf":
        layout_name = None if sheet.layout_name in (None, "Model") else sheet.layout_name
        data = run_sandboxed(text_parsing.dxf_text, payload, layout_name)
        method = METHOD_CAD
    else:
        data = run_sandboxed(text_parsing.pdf_text, payload, sheet.index_in_document)
        method = METHOD_TEXT_LAYER

    spans = [Span(*span) for span in data["spans"]]
    page = Box(*data["page"])
    best = SheetReading(read(spans, page), method, spans, page)

    # No text layer, or none that says what the sheet is: OCR the corners a title block sits
    # in, most likely first, and stop at the first confident reading.
    if document.kind == "pdf" and best.reading.needs_help(DEFAULT_THRESHOLD):
        for region in CANDIDATES:
            try:
                ocr = run_sandboxed(
                    text_parsing.ocr_text,
                    payload,
                    "pdf",
                    sheet.index_in_document,
                    [region.x0, region.y0, region.x1, region.y1],
                )
            except SandboxFailure as failure:
                log.warning("title_block_ocr_failed", sheet_id=str(sheet.id), reason=failure.reason)
                break
            ocr_spans = [Span(*span) for span in ocr["spans"]]
            candidate = SheetReading(
                read(ocr_spans, Box(*ocr["page"])),
                METHOD_OCR,
                ocr_spans,
                Box(*ocr["page"]),
                float(ocr["mean_confidence"]),
            )
            if candidate.reading.confidence >= best.reading.confidence:
                best = candidate
            if not best.reading.needs_help(DEFAULT_THRESHOLD):
                break

    return _with_remembered_layout(session, document, best)


def _with_remembered_layout(
    session: Session, document: Document, found: SheetReading
) -> SheetReading:
    """Read by position when this consultant's layout is known and the page matches it."""
    organisation_id = session.execute(
        select(Bid.organisation_id).where(Bid.id == document.bid_id)
    ).scalar_one()
    marks = fingerprint(found.spans, found.page)
    if not marks:
        return found
    for remembered in session.execute(
        select(TitleBlockLayout).where(TitleBlockLayout.organisation_id == organisation_id)
    ).scalars():
        if similarity(marks, stored_fingerprint(remembered.fingerprint)) < LAYOUT_MATCH:
            continue
        reading = read_with_layout(found.spans, found.page, Layout.from_json(remembered.layout))
        if reading.confidence >= found.reading.confidence:
            remembered.times_used += 1
            log.info("title_block_layout_used", consultant=remembered.consultant)
            return SheetReading(
                reading, METHOD_LAYOUT, found.spans, found.page, found.ocr_confidence
            )
    return found


def stored_fingerprint(raw: list[list[object]]) -> frozenset[tuple[str, int, int]]:
    """A fingerprint as JSON keeps it: lists of [field, x, y]."""
    return frozenset((str(mark[0]), int(str(mark[1])), int(str(mark[2]))) for mark in raw)


def _store(
    session: Session, document: Document, sheet: Sheet, found: SheetReading
) -> SheetRevision:
    reading = found.reading
    number = reading.value(Field.SHEET_NUMBER)
    label = reading.value(Field.REVISION)

    if number and label:
        same = session.execute(
            select(SheetRevision).where(
                SheetRevision.bid_id == document.bid_id,
                SheetRevision.sheet_number == number,
                SheetRevision.revision_label == label,
            )
        ).scalar_one_or_none()
        if same is not None:
            # The same drawing at the same revision in another file, typically the PDF of a
            # DWG. One revision with two renditions, not two revisions competing for Current.
            if str(sheet.id) not in same.alternate_sheet_ids:
                same.alternate_sheet_ids = [*same.alternate_sheet_ids, str(sheet.id)]
            log.info("title_block_rendition", sheet_id=str(sheet.id), revision_id=str(same.id))
            return same

    revision = SheetRevision(
        bid_id=document.bid_id,
        sheet_id=sheet.id,
        sheet_number=number,
        revision_label=label,
        title=_short(reading.value(Field.TITLE), 300),
        revision_date=reading.revision_date,
        discipline=reading.discipline,
        level=_short(reading.value(Field.LEVEL), 40),
        zone=_short(reading.value(Field.ZONE), 40),
        scale_text=_short(reading.value(Field.SCALE), 40),
        extraction_method=found.method,
        source_confidence=reading.confidence,
        reading=reading_json(reading, found.method),
        sources={"title_block": label},
        created_by_id=document.created_by_id,
    )
    session.add(revision)
    session.flush()
    log.info(
        "title_block_read",
        sheet_id=str(sheet.id),
        method=found.method,
        confidence=reading.confidence,
    )
    return revision


def _short(value: str | None, limit: int) -> str | None:
    return value[:limit] if value else value


def reading_json(reading: TitleBlockReading, method: str) -> dict[str, Any]:
    """A reading as stored: every field with its value, confidence, method and position."""

    def dump(found: FieldReading) -> dict[str, Any]:
        box = found.box
        return {
            "value": found.value,
            "confidence": found.confidence,
            "how": found.how,
            "box": None if box is None else [box.x0, box.y0, box.x1, box.y1],
        }

    region = reading.region
    return {
        "method": method,
        "fields": {str(name): dump(found) for name, found in reading.fields.items()},
        "history": list(reading.history),
        "region": None if region is None else [region.x0, region.y0, region.x1, region.y1],
    }


def _ask_for_help(
    session: Session,
    store: ObjectStore,
    document: Document,
    sheet: Sheet,
    payload: bytes,
    revision: SheetRevision,
    found: SheetReading,
) -> None:
    """Queue the model check with a crop, or, for CAD text, go straight to a person.

    CAD text is exact; a model cannot read an entity better than ezdxf did, so an unclear
    DXF title block is a question for a person, not for a model.
    """
    from firebid.jobs.enqueue import enqueue
    from firebid.jobs.tasks import check_title_block
    from firebid.parsing import text as text_parsing

    reading = revision.reading or {}
    if document.kind != "pdf":
        task = raise_review(session, revision, "the title block text is unclear")
        revision.reading = {**reading, "review_task_id": str(task.id)}
        return

    region = found.reading.region
    corner = region.relative_to(found.page).padded(0.02) if region is not None else CANDIDATES[0]
    bounds = [max(corner.x0, 0.0), max(corner.y0, 0.0), min(corner.x1, 1.0), min(corner.y1, 1.0)]
    try:
        png = run_sandboxed(text_parsing.crop_png, payload, "pdf", sheet.index_in_document, bounds)
    except SandboxFailure as failure:
        task = raise_review(
            session, revision, f"the title block could not be cropped: {failure.reason}"
        )
        revision.reading = {**reading, "review_task_id": str(task.id)}
        return

    key = f"crops/title-blocks/{hashlib.sha256(png).hexdigest()}.png"
    with contextlib.suppress(ObjectExists):  # the same crop, stored by an earlier run
        store.put_once(key, png, content_type="image/png")
    text = "\n".join(span.clean for span in found.spans if region is None or region.contains(span))
    revision.reading = {**reading, "crop_key": key, "page_text": text[:4000]}
    enqueue(
        session,
        check_title_block,
        revision_id=str(revision.id),
        user_id=str(document.created_by_id) if document.created_by_id else "",
    )


def raise_review(session: Session, revision: SheetRevision, why: str) -> Any:
    """A review queue item asking a person to confirm or correct a title block (FR-DOC-02)."""
    from firebid.db.models.workflow import HumanTask

    task = HumanTask(
        bid_id=revision.bid_id,
        kind="title_block_review",
        title=f"Check the title block of {revision.sheet_number or 'an unidentified sheet'}",
        required_role="estimator",
        payload={
            "sheet_revision_id": str(revision.id),
            "sheet_id": str(revision.sheet_id),
            "why": why,
            "reading": revision.reading,
        },
    )
    session.add(task)
    session.flush()
    log.info("title_block_review_raised", revision_id=str(revision.id), why=why)
    return task


# --- The model check, on the ordinary worker -------------------------------------------------


def check_with_model(
    session: Session, store: ObjectStore, revision: SheetRevision, router: Any
) -> SheetRevision:
    """Ask `title_block_reader` about a reading that was not confident enough.

    Fields the model is surer of than the deterministic reading replace it. When the model
    is unsure too, `run_agent` raises a human task, which is where this ends: with a person.
    """
    from firebid.agents.base import AgentInput
    from firebid.agents.runtime import Escalated, run_agent_with_result
    from firebid.agents.title_block import TitleBlock, TitleBlockInput, TitleBlockReader

    reading = dict(revision.reading or {})
    key = reading.get("crop_key")
    if not isinstance(key, str):
        return revision
    sheet = session.get(Sheet, revision.sheet_id)
    document = session.get(Document, sheet.document_id) if sheet else None
    request = AgentInput(
        bid_id=revision.bid_id,
        idempotency_key=f"title_block:{revision.id}:{_fingerprint_of(reading)}",
        payload=TitleBlockInput(
            image_png=store.get(key),
            page_text=str(reading.get("page_text") or ""),
            sheet_hint=document.filename if document else None,
        ),
    )
    agent = TitleBlockReader(router)
    try:
        run, result = run_agent_with_result(session, agent, request)
    except Escalated as escalation:
        revision.reading = {**reading, "review_task_id": str(escalation.task.id)}
        session.flush()
        return revision

    if result is None or not isinstance(result.output, TitleBlock):
        return revision  # a redelivery: the first delivery merged the result already
    _merge_model_reading(revision, result.output, str(run.id))
    session.flush()
    if (revision.source_confidence or 0.0) >= DEFAULT_THRESHOLD:
        from firebid.services.revisions import settle

        settle(session, revision)
    return revision


def _fingerprint_of(reading: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(reading.get("fields", {}), sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def _merge_model_reading(revision: SheetRevision, block: Any, run_id: str) -> None:
    """Take each field the model is surer of; record that the model supplied it."""
    reading = dict(revision.reading or {})
    fields: dict[str, dict[str, Any]] = dict(cast(dict[str, Any], reading.get("fields") or {}))
    pairs = (
        (Field.SHEET_NUMBER, block.sheet_number, block.sheet_number_confidence),
        (Field.REVISION, block.revision, block.revision_confidence),
        (Field.TITLE, block.sheet_title, block.sheet_title_confidence),
        (Field.SCALE, block.scale, block.scale_confidence),
    )
    for name, value, confidence in pairs:
        current = fields.get(str(name), {})
        if value and confidence > float(current.get("confidence", 0.0)):
            fields[str(name)] = {
                "value": value,
                "confidence": confidence,
                "how": METHOD_MODEL,
                "box": None,
                "agent_run_id": run_id,
            }
    reading["fields"] = fields

    def value_of(name: Field) -> str | None:
        found = fields.get(str(name), {}).get("value")
        return str(found) if found else None

    revision.sheet_number = value_of(Field.SHEET_NUMBER)
    revision.revision_label = value_of(Field.REVISION)
    revision.title = _short(value_of(Field.TITLE), 300)
    revision.scale_text = _short(value_of(Field.SCALE), 40)
    if block.date and revision.revision_date is None:
        revision.revision_date = parse_date(block.date)
    revision.extraction_method = METHOD_MODEL
    revision.source_confidence = min(
        float(fields.get(str(name), {}).get("confidence", 0.0)) for name in CRITICAL
    )
    revision.reading = reading
    sources = dict(revision.sources or {})
    sources["title_block"] = revision.revision_label
    revision.sources = sources
