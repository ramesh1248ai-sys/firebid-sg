"""Each sheet's geometry: extracted once, cached by content, indexed by place (FR-VIS-01).

Runs beside the parse job in the sandbox pool, because extraction opens the tender file. The
stage cache comes first: a sheet whose content hash and extractor version have been seen
before, on this bid or any other, is loaded rather than parsed. Then the sheet's record and
its spatial index are written for this bid.
"""

from __future__ import annotations

import contextlib
import json
import time
from typing import Any

import pyarrow as pa
import structlog
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from firebid.db.models.documents import Document, Sheet
from firebid.db.models.drawings import GeometryFeature, SheetGeometry
from firebid.drawings import geometry
from firebid.drawings.stage_cache import StageCache
from firebid.sandbox.runner import run_sandboxed
from firebid.services.pages import page_index
from firebid.storage.object_store import ObjectStore

log = structlog.get_logger("firebid.geometry")

STAGE = "geometry"
PARQUET = "application/vnd.apache.parquet"
METADATA_KEY = b"firebid"
# Primitives worth finding by place. Line work is queried from the Parquet file itself.
INDEXED = ("text", "insert", "circle", "dimension")


def cache(store: ObjectStore) -> StageCache:
    return StageCache(store, STAGE, geometry.EXTRACTOR_VERSION)


def extract_sheet(
    session: Session, store: ObjectStore, document: Document, sheet: Sheet, payload: bytes | None
) -> SheetGeometry:
    """The sheet's geometry, from the cache when it can be, from the file when it must."""
    existing = session.execute(
        select(SheetGeometry).where(SheetGeometry.sheet_id == sheet.id)
    ).scalar_one_or_none()
    if existing is not None and existing.extractor_version == geometry.EXTRACTOR_VERSION:
        return existing

    if not sheet.content_hash:
        raise ValueError("a sheet is extracted after its content hash is known")
    stage = cache(store)
    started = time.perf_counter()
    cached = stage.get(sheet.content_hash)
    if cached is not None:
        table = geometry.from_parquet(cached)
        from_cache = True
    else:
        if payload is None:
            payload = store.get(document.storage_key)
        table = _extract(document, sheet, payload)
        stage.put(sheet.content_hash, geometry.to_parquet(table), PARQUET)
        from_cache = False
    seconds = time.perf_counter() - started
    meta = _metadata(table)

    record = existing or SheetGeometry(bid_id=sheet.bid_id, sheet_id=sheet.id)
    record.content_hash = sheet.content_hash
    record.extractor_version = geometry.EXTRACTOR_VERSION
    record.object_key = stage.key(sheet.content_hash)
    record.method = str(meta.get("method", "unknown"))
    record.counts = geometry.counts(table)
    record.page = list(meta.get("page") or [0.0, 0.0, sheet.width_mm or 0, sheet.height_mm or 0])
    record.views = list(meta.get("views") or [])
    record.seconds = round(seconds, 4)
    record.from_cache = from_cache
    record.note = meta.get("primary_failure") or meta.get("ocr_note")
    if existing is None:
        session.add(record)
    indexing = time.perf_counter()
    _index(session, sheet, table)
    session.flush()
    log.info(
        "geometry_extracted",
        sheet_id=str(sheet.id),
        from_cache=from_cache,
        seconds=record.seconds,
        # Writing the spatial index is outside `seconds` (the extraction); logged apart so
        # a slow write shows up as one (P1-11 benchmark).
        index_seconds=round(time.perf_counter() - indexing, 3),
        primitives=table.num_rows,
    )
    return record


def _extract(document: Document, sheet: Sheet, payload: bytes) -> pa.Table:
    from firebid.parsing import geometry_dxf, geometry_pdf

    if document.kind == "dxf":
        layout = None if sheet.layout_name in (None, "Model") else sheet.layout_name
        result: dict[str, Any] = run_sandboxed(geometry_dxf.extract, payload, layout)
    else:
        result = run_sandboxed(geometry_pdf.extract, payload, page_index(sheet, payload))
        result.setdefault(
            "page", [0.0, 0.0, float(sheet.width_mm or 0), float(sheet.height_mm or 0)]
        )
    table = geometry.from_parquet(result["parquet"])
    meta = {key: value for key, value in result.items() if key != "parquet"}
    return table.replace_schema_metadata({METADATA_KEY: json.dumps(meta).encode()})


def _metadata(table: pa.Table) -> dict[str, Any]:
    raw = (table.schema.metadata or {}).get(METADATA_KEY)
    return json.loads(raw) if raw else {}


def _index(session: Session, sheet: Sheet, table: pa.Table) -> None:
    """Replace the sheet's spatial index with this geometry's findable primitives."""
    session.execute(delete(GeometryFeature).where(GeometryFeature.sheet_id == sheet.id))
    columns = table.select(["kind", "layer", "text", "block", "minx", "miny", "maxx", "maxy"])
    rows = []
    for row, item in enumerate(columns.to_pylist()):
        if item["kind"] not in INDEXED or item["minx"] is None:
            continue
        rows.append(
            {
                "bid_id": sheet.bid_id,
                "sheet_id": sheet.id,
                "row": row,
                "kind": item["kind"],
                "layer": (item["layer"] or None) and str(item["layer"])[:120],
                "label": (item["text"] or item["block"] or None)
                and str(item["text"] or item["block"])[:300],
                "bbox": (item["minx"], item["miny"], item["maxx"], item["maxy"]),
            }
        )
    if rows:
        session.execute(insert(GeometryFeature), rows)


def load(store: ObjectStore, record: SheetGeometry) -> pa.Table:
    """A sheet's primitives, from the file its record points at."""
    return geometry.from_parquet(store.get(record.object_key))


def _uncached(store: ObjectStore, sheets: list[Sheet]) -> list[Sheet]:
    stage = cache(store)
    seen: set[str] = set()
    missing = []
    for sheet in sheets:
        digest = sheet.content_hash
        if digest and digest not in seen and not store.exists(stage.key(digest)):
            seen.add(digest)
            missing.append(sheet)
    return missing


def _extract_ahead(
    store: ObjectStore, document: Document, sheets: list[Sheet], payload: bytes
) -> None:
    """Extract sheets into the stage cache several at a time (NFR-01; P1-11).

    Each extraction is its own sandboxed process, so threads that wait on them run the
    sheets truly in parallel, as many as `parse_concurrency` allows. Only the cache is
    written here; the loop after it records every sheet from the cache, in order, in the
    job's session. A sheet that fails here is simply tried again there, which records why.
    """
    from concurrent.futures import ThreadPoolExecutor

    from firebid.sandbox.runner import SandboxFailure
    from firebid.settings import get_settings

    stage = cache(store)

    def one(sheet: Sheet) -> None:
        with contextlib.suppress(SandboxFailure):
            table = _extract(document, sheet, payload)
            stage.put(str(sheet.content_hash), geometry.to_parquet(table), PARQUET)

    workers = max(1, get_settings().parse_concurrency)
    if workers == 1 or len(sheets) == 1:
        return  # nothing to overlap: the loop extracts as it goes
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="geometry") as pool:
        list(pool.map(one, sheets))


def extract_all(
    session: Session, store: ObjectStore, document: Document, sheets: list[Sheet]
) -> list[SheetGeometry]:
    """Every sheet of a document. One sheet that fails is recorded and does not stop the rest."""
    from firebid.sandbox.runner import SandboxFailure

    payload: bytes | None = None
    missing = _uncached(store, sheets)
    if missing:
        payload = store.get(document.storage_key)
        _extract_ahead(store, document, missing, payload)
    records = []
    for sheet in sheets:
        try:
            if payload is None and not store.exists(cache(store).key(sheet.content_hash or "")):
                payload = store.get(document.storage_key)
            records.append(extract_sheet(session, store, document, sheet, payload))
        except SandboxFailure as failure:
            log.warning("geometry_failed", sheet_id=str(sheet.id), reason=failure.reason)
    return records
