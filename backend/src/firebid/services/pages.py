"""A sheet's own page, cut from its document once, so reading a sheet fetches one page.

A tender set is often one PDF of a hundred sheets or more. Each sheet is read in its own job
(ADR-010), and each job used to fetch the whole file from object storage and hand all of it
to every parser it started: a 121-sheet, 83 MB set was downloaded 121 times. When a PDF's
sheets are registered it is now cut into one single-page PDF per sheet, stored beside the
original. A sheet's job, and a close-up tile's render, fetch only that.

The original is untouched and stays the record (guardrail: originals are immutable). A page
file is derived from it and keyed by its checksum, so it is written once and can always be
made again. Where there is none (a DXF, a one-page PDF, a document registered before this
existed, a split that failed) the whole document is used, exactly as before.

`PagePayload` is how the bytes say which they are: a page file is page 0 of itself, and the
whole document is read at the sheet's own index. `page_index` is the only place that knows.
"""

from __future__ import annotations

import contextlib

import structlog

from firebid.db.models.documents import Document, Sheet
from firebid.sandbox.runner import SandboxFailure, run_sandboxed
from firebid.storage.object_store import ObjectExists, ObjectStore

log = structlog.get_logger("firebid.pages")

PDF = "application/pdf"
# Pages cut per sandbox call: bounds what one call holds and returns at once.
BATCH = 20


class PagePayload(bytes):
    """One sheet's own page as a single-page PDF: page 0 of itself."""


def page_key(document: Document, index: int) -> str:
    """Where a sheet's page file is stored: by the document's checksum and the page."""
    return f"bids/{document.bid_id}/pages/{document.sha256}/{index:05d}.pdf"


def page_index(sheet: Sheet, payload: bytes) -> int:
    """The page to read in `payload` for this sheet."""
    return 0 if isinstance(payload, PagePayload) else sheet.index_in_document


def store_pages(store: ObjectStore, document: Document, payload: bytes, count: int) -> int:
    """Cut a PDF into its pages and store each. Returns how many page files now exist.

    Idempotent: a page already stored is left alone. A failure costs nothing but speed: the
    sheets are read from the whole document, and it is logged.
    """
    if document.kind != "pdf" or count < 2:
        return 0
    from firebid.parsing import pdf as pdf_parsing

    missing = [i for i in range(count) if not store.exists(page_key(document, i))]
    try:
        for start in range(0, len(missing), BATCH):
            cut = run_sandboxed(pdf_parsing.split_pages, payload, missing[start : start + BATCH])
            for index, page in cut.items():
                with contextlib.suppress(ObjectExists):  # another run stored it meanwhile
                    store.put_once(page_key(document, index), page, content_type=PDF)
    except SandboxFailure as failure:
        log.warning("pages_not_cut", document_id=str(document.id), reason=failure.reason)
        return count - len(missing)
    log.info("pages_cut", document_id=str(document.id), pages=count, written=len(missing))
    return count


def sheet_payload(store: ObjectStore, document: Document, sheet: Sheet) -> bytes:
    """The bytes to read this sheet from: its own page when there is one, else the document."""
    if document.kind == "pdf":
        key = page_key(document, sheet.index_in_document)
        if store.exists(key):
            return PagePayload(store.get(key))
    return store.get(document.storage_key)
