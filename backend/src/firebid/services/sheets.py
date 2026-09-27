"""Turning a stored document into sheets a viewer can open.

One document becomes one Sheet per PDF page or per CAD layout. Each sheet gets its paper
size, what it is made of, the lineage back to the file it came from (FR-DOC-07), and the low
levels of its tile pyramid so the viewer opens instantly (ADR-005).

Three rules shape this module:

* **Parsing happens in the sandbox, nowhere else.** This module holds the database session
  and the object store, so it must never open a file itself; it asks the sandbox for facts
  and pixels and does the bookkeeping with what comes back.
* **A failure is a state, not an exception.** A page that cannot be rendered leaves the
  document `rejected` with a reason. A tender set where one sheet vanished is worse than one
  that refused it loudly.
* **Re-running is cheap.** Sheets are keyed by document and page, and tiles by content hash,
  so processing a document twice re-uses everything and changes nothing (the stage-caching
  convention). Job delivery is at-least-once, so this has to hold.
"""

from __future__ import annotations

import contextlib
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.models.documents import Document, Sheet
from firebid.domain.evidence import SourceRef
from firebid.imaging.pyramid import (
    PRE_RENDERED_MAX_PIXELS,
    RENDERER_VERSION,
    Pyramid,
    base_pixels,
    content_hash,
)
from firebid.sandbox.runner import SandboxFailure, run_sandboxed
from firebid.storage.object_store import ObjectExists, ObjectStore

log = structlog.get_logger("firebid.sheets")


@dataclass
class ProcessOutcome:
    """What processing one document produced."""

    sheets: list[Sheet] = field(default_factory=list)
    tiles_written: int = 0
    reused: bool = False
    failure: str | None = None


def _fit_within(width_px: int, height_px: int, limit: int) -> tuple[int, int]:
    """The largest size within `limit` that keeps the sheet's proportions."""
    longest = max(width_px, height_px)
    if longest <= limit:
        return width_px, height_px
    shrink = limit / longest
    return max(1, round(width_px * shrink)), max(1, round(height_px * shrink))


def existing_sheet(session: Session, document_id: uuid.UUID, index: int) -> Sheet | None:
    return session.execute(
        select(Sheet).where(Sheet.document_id == document_id, Sheet.index_in_document == index)
    ).scalar_one_or_none()


def process_document(session: Session, store: ObjectStore, document: Document) -> ProcessOutcome:
    """Take one stored document to `done`, or to `rejected` with a reason.

    Only a document that has been scanned clean is processed: this is the point the
    store-scan-parse order (guardrail 9) is enforced for the parsing half.
    """
    outcome = ProcessOutcome()

    if document.state == "quarantined":
        outcome.failure = "this file is quarantined and is never opened"
        return outcome
    if document.state == "awaiting_scan":
        outcome.failure = "this file has not been scanned yet, so it is not opened"
        return outcome
    if document.scanned_at is None:
        outcome.failure = "this file has no record of being scanned, so it is not opened"
        return outcome

    payload = store.get(document.storage_key)
    document.state = "processing"
    session.flush()

    try:
        pages = _read_pages(document, payload)
    except SandboxFailure as failure:
        document.state = "rejected"
        document.rejected_reason = failure.reason
        outcome.failure = failure.reason
        log.warning("document_rejected", document_id=str(document.id), reason=failure.reason)
        session.flush()
        return outcome

    for page in pages:
        sheet, written = _make_sheet(session, store, document, payload, page)
        outcome.sheets.append(sheet)
        outcome.tiles_written += written

    document.state = "done"
    document.rejected_reason = None
    session.flush()
    log.info(
        "document_processed",
        document_id=str(document.id),
        sheets=len(outcome.sheets),
        tiles=outcome.tiles_written,
    )
    return outcome


def _read_pages(document: Document, payload: bytes) -> list[dict[str, Any]]:
    """Ask the sandbox what is in this file. One call, whatever the format."""
    from firebid.parsing import dxf as dxf_parsing
    from firebid.parsing import pdf as pdf_parsing

    if document.kind == "pdf":
        return run_sandboxed(pdf_parsing.inspect_pdf, payload)
    if document.kind == "dxf":
        return run_sandboxed(dxf_parsing.inspect_dxf, payload)
    raise SandboxFailure(
        "error", f"'{document.kind}' files do not become sheets", detail="unsupported kind"
    )


def _make_sheet(
    session: Session,
    store: ObjectStore,
    document: Document,
    payload: bytes,
    page: dict[str, Any],
) -> tuple[Sheet, int]:
    index = int(page["index"])
    width_mm = float(page["width_mm"])
    height_mm = float(page["height_mm"])
    width_px, height_px = base_pixels(width_mm, height_mm)
    sheet_hash = content_hash(document.sha256, index, width_px, height_px)
    pyramid = Pyramid(width_px=width_px, height_px=height_px)

    sheet = existing_sheet(session, document.id, index)
    if sheet is None:
        sheet = Sheet(bid_id=document.bid_id, document_id=document.id, index_in_document=index)
        session.add(sheet)

    sheet.layout_name = str(page.get("label") or f"page {index + 1}")
    sheet.width_mm = width_mm
    sheet.height_mm = height_mm
    sheet.content_class = str(page.get("content_class") or "vector")
    sheet.quality_detail = {
        key: value
        for key, value in page.items()
        if key not in ("index", "width_mm", "height_mm", "content_class", "label")
    }
    sheet.content_hash = sheet_hash
    sheet.base_width_px = width_px
    sheet.base_height_px = height_px
    sheet.max_level = pyramid.max_level
    sheet.renderer_version = RENDERER_VERSION
    sheet.source_ref = SourceRef(
        document_id=document.id,
        page_or_layout=sheet.layout_name,
    ).model_dump(mode="json")
    session.flush()

    written = _render_low_levels(store, document, payload, sheet, pyramid, sheet_hash)
    return sheet, written


def _render_low_levels(
    store: ObjectStore,
    document: Document,
    payload: bytes,
    sheet: Sheet,
    pyramid: Pyramid,
    sheet_hash: str,
) -> int:
    """Render and store the levels the viewer needs immediately.

    Already-stored tiles are left alone: the key is the content hash, so what is there is
    already these pixels, and re-running after an addendum costs nothing for unchanged sheets.
    """
    from firebid.imaging.pyramid import thumbnail_key

    if store.exists(thumbnail_key(sheet_hash)):
        sheet.thumbnail_key = thumbnail_key(sheet_hash)
        log.debug("tiles_reused", sheet_id=str(sheet.id), content_hash=sheet_hash)
        return 0

    render_width, render_height = _fit_within(
        pyramid.width_px, pyramid.height_px, PRE_RENDERED_MAX_PIXELS
    )
    tiles = run_sandboxed(
        _render_and_cut,
        document.kind,
        payload,
        sheet.index_in_document,
        render_width,
        render_height,
        pyramid.width_px,
        pyramid.height_px,
        sheet_hash,
    )

    written = 0
    for key, tile in tiles.items():
        try:
            store.put_once(key, tile, content_type="image/webp")
            written += 1
        except ObjectExists:
            # Another worker rendered the same sheet concurrently. Same hash, same pixels.
            pass

    sheet.thumbnail_key = thumbnail_key(sheet_hash)
    return written


def _render_and_cut(
    kind: str,
    payload: bytes,
    index: int,
    render_width: int,
    render_height: int,
    base_width: int,
    base_height: int,
    sheet_hash: str,
) -> dict[str, bytes]:
    """Runs in the sandbox: render one sheet once, then cut every pre-rendered level from it."""
    from PIL import Image

    from firebid.imaging.pyramid import Pyramid
    from firebid.imaging.tiles import pre_render

    rendered = _render(kind, payload, index, render_width, render_height)
    image = Image.frombytes("RGB", (rendered["width"], rendered["height"]), rendered["pixels"])
    return pre_render(image, Pyramid(width_px=base_width, height_px=base_height), sheet_hash)


def _render(kind: str, payload: bytes, index: int, width_px: int, height_px: int) -> dict[str, Any]:
    """Dispatch to the renderer for this format. Runs in the sandbox."""
    if kind == "pdf":
        from firebid.parsing.pdf import render_page

        return render_page(payload, index, width_px, height_px)
    if kind == "dxf":
        from firebid.parsing.dxf import render_layout

        return render_layout(payload, index, width_px, height_px)
    raise ValueError(f"there is no renderer for '{kind}' files")


def render_tile(
    store: ObjectStore,
    document_payload: bytes,
    document_kind: str,
    sheet: Sheet,
    level: int,
    column: int,
    row: int,
) -> bytes:
    """One close-up tile, rendered on first request and cached (ADR-005).

    The cache is checked first, so a second viewer panning over the same area pays nothing.
    """
    from firebid.imaging.pyramid import tile_key

    if sheet.content_hash is None or sheet.base_width_px is None or sheet.base_height_px is None:
        raise ValueError("this sheet has not been processed yet")

    key = tile_key(sheet.content_hash, level, column, row)
    if store.exists(key):
        return store.get(key)

    tile = run_sandboxed(
        _render_one_tile,
        document_kind,
        document_payload,
        sheet.index_in_document,
        sheet.base_width_px,
        sheet.base_height_px,
        level,
        column,
        row,
    )
    with contextlib.suppress(ObjectExists):
        store.put_once(key, tile, content_type="image/webp")
    log.debug("tile_rendered", sheet_id=str(sheet.id), level=level, column=column, row=row)
    return tile


def _render_one_tile(
    kind: str,
    payload: bytes,
    index: int,
    base_width: int,
    base_height: int,
    level: int,
    column: int,
    row: int,
) -> bytes:
    """Runs in the sandbox: render just the level this tile is on, then cut the tile out.

    Rendering a whole close-up level to produce one tile sounds wasteful, and would be if
    tiles were requested alone. They are not: OpenSeadragon asks for every tile covering the
    viewport at once, and they all land in the cache from the first render.
    """
    from PIL import Image

    from firebid.imaging.pyramid import Pyramid
    from firebid.imaging.tiles import cut_one

    pyramid = Pyramid(width_px=base_width, height_px=base_height)
    level_width, level_height = pyramid.size_at(level)
    rendered = _render(kind, payload, index, level_width, level_height)
    image = Image.frombytes("RGB", (rendered["width"], rendered["height"]), rendered["pixels"])
    return cut_one(image, pyramid, level, column, row)
