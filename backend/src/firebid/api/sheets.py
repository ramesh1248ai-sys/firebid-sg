"""Sheets and their tiles: what the viewer asks for (FR-DOC-01, FR-DOC-07, ADR-005).

A sheet is one page of a PDF or one layout of a CAD file. The viewer opens it by reading the
descriptor, then asking for the tiles covering what is on screen.

**Authorisation is on the sheet, not the tile key.** Tiles are stored by content hash so an
identical drawing on two bids is stored once, which means the key alone cannot say who may
see it. Every request resolves the sheet through `CurrentBid` first, so a caller who is not
on the bid gets a 404 whether or not they can guess a hash.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Response, status
from pydantic import BaseModel
from sqlalchemy import select

from firebid.api.deps import CurrentBid, DbSession
from firebid.db.models.documents import Document, Sheet
from firebid.imaging.pyramid import Pyramid, descriptor, thumbnail_key, tile_key
from firebid.storage.object_store import get_object_store

router = APIRouter(prefix="/bids/{bid_id}/sheets", tags=["sheets"])

# A tile is immutable: its key contains the content hash and the renderer version, so a
# change to either produces a different URL. A year is the longest `max-age` browsers honour.
TILE_CACHE_CONTROL = "private, max-age=31536000, immutable"


class SheetOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    # The file the sheet came from, which is how an estimator recognises it until P1-02
    # reads the sheet number off the title block.
    filename: str = ""
    index_in_document: int
    layout_name: str | None
    width_mm: float | None
    height_mm: float | None
    content_class: str | None
    max_level: int | None
    base_width_px: int | None
    base_height_px: int | None
    has_thumbnail: bool = False
    source_ref: dict[str, object] | None = None
    # What the source lets the platform promise (FR-DOC-06).
    quality_band: str | None = None
    manual_takeoff_recommended: bool = False

    model_config = {"from_attributes": True}


class SheetDetail(SheetOut):
    tile_source: dict[str, object] | None = None
    quality_detail: dict[str, object] | None = None


def _as_out(sheet: Sheet, filename: str) -> SheetOut:
    out = SheetOut.model_validate(sheet)
    out.filename = filename
    out.has_thumbnail = sheet.thumbnail_key is not None
    return out


def _resolve(session: DbSession, context: CurrentBid, sheet_id: uuid.UUID) -> Sheet:
    sheet = session.get(Sheet, sheet_id)
    if sheet is None or sheet.bid_id != context.bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
    return sheet


@router.get("", response_model=list[SheetOut])
def list_sheets(
    context: CurrentBid, session: DbSession, document_id: uuid.UUID | None = None
) -> list[SheetOut]:
    """Every sheet on the bid, or just one document's, in the order they appear in the file."""
    query = (
        select(Sheet, Document.filename)
        .join(Document, Document.id == Sheet.document_id)
        .where(Sheet.bid_id == context.bid.id)
    )
    if document_id is not None:
        query = query.where(Sheet.document_id == document_id)
    rows = session.execute(
        query.order_by(Document.filename, Sheet.document_id, Sheet.index_in_document)
    ).all()
    return [_as_out(sheet, filename) for sheet, filename in rows]


@router.get("/{sheet_id}", response_model=SheetDetail)
def get_sheet(context: CurrentBid, session: DbSession, sheet_id: uuid.UUID) -> SheetDetail:
    sheet = _resolve(session, context, sheet_id)
    detail = SheetDetail.model_validate(sheet)
    document = session.get(Document, sheet.document_id)
    detail.filename = document.filename if document else ""
    detail.has_thumbnail = sheet.thumbnail_key is not None
    detail.quality_detail = sheet.quality_detail
    if sheet.content_hash and sheet.base_width_px and sheet.base_height_px:
        pyramid = Pyramid(width_px=sheet.base_width_px, height_px=sheet.base_height_px)
        detail.tile_source = {
            **descriptor(pyramid, sheet.content_hash),
            "tileUrl": f"/bids/{sheet.bid_id}/sheets/{sheet.id}/tiles",
        }
    return detail


@router.get("/{sheet_id}/thumbnail.webp")
def thumbnail(context: CurrentBid, session: DbSession, sheet_id: uuid.UUID) -> Response:
    sheet = _resolve(session, context, sheet_id)
    if sheet.content_hash is None or sheet.thumbnail_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this sheet has no thumbnail yet")

    store = get_object_store()
    key = thumbnail_key(sheet.content_hash)
    if not store.exists(key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this sheet has no thumbnail yet")
    return _image_response(store.get(key))


@router.get("/{sheet_id}/tiles/{level}/{column}_{row}.webp")
def tile(
    context: CurrentBid,
    session: DbSession,
    sheet_id: uuid.UUID,
    level: Annotated[int, Path(ge=0, le=32)],
    column: Annotated[int, Path(ge=0, le=100_000)],
    row: Annotated[int, Path(ge=0, le=100_000)],
) -> Response:
    """One tile. Served from the cache, or rendered in the sandbox on first request.

    A level that was pre-rendered is always a cache read. A close-up level is rendered here
    the first time anyone looks at that part of the sheet, and cached for everyone after.
    """
    sheet = _resolve(session, context, sheet_id)
    if sheet.content_hash is None or sheet.base_width_px is None or sheet.base_height_px is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "this sheet has not finished processing yet")

    pyramid = Pyramid(width_px=sheet.base_width_px, height_px=sheet.base_height_px)
    if not pyramid.holds(level, column, row):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such tile on this sheet")

    store = get_object_store()
    key = tile_key(sheet.content_hash, level, column, row)
    if store.exists(key):
        return _image_response(store.get(key))

    document = session.get(Document, sheet.document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "the sheet's document is gone")

    from firebid.sandbox.runner import SandboxFailure
    from firebid.services.sheets import render_tile

    try:
        payload = render_tile(
            store,
            store.get(document.storage_key),
            str(document.kind),
            sheet,
            level,
            column,
            row,
        )
    except SandboxFailure as failure:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"this part of the sheet could not be rendered: {failure.reason}",
        ) from failure
    return _image_response(payload)


def _image_response(payload: bytes) -> Response:
    return Response(
        content=payload,
        media_type="image/webp",
        headers={
            "Cache-Control": TILE_CACHE_CONTROL,
            "ETag": f'"{hashlib.sha256(payload).hexdigest()[:32]}"',
        },
    )
