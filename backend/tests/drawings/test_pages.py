"""A sheet's own page: cut from its document once, and read exactly as it was in it (NFR-01).

A two-sheet PDF is made from two different synthetic plans. Each page, cut out as a PDF of
its own, must give the same geometry, the same text and the same pixels as that page read
in the whole document: the cut is for speed, and nothing a sheet's reading depends on may
change with it.
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from firebid.db.models.documents import Document, Sheet
from firebid.drawings import geometry
from firebid.evals import synthetic
from firebid.parsing import geometry_pdf
from firebid.parsing import pdf as pdf_parsing
from firebid.parsing import text as text_parsing
from firebid.services import pages
from firebid.services.pages import PagePayload
from firebid.storage.object_store import MemoryObjectStore

pytestmark = pytest.mark.req("NFR-01")


@pytest.fixture(scope="module")
def tender(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """Two different sheets in one PDF, as a consultant issues a set."""
    folder = tmp_path_factory.mktemp("tender")
    first, _ = synthetic.general_arrangement("FP-L05-201")
    second, _ = synthetic.general_arrangement("FP-L06-201", columns=5, rows=4)
    merged = pdfium.PdfDocument.new()
    for index, drawing in enumerate((first, second)):
        path = synthetic.write_pdf(drawing, Path(folder) / f"{index}.pdf", live_text=True)
        source = pdfium.PdfDocument(path.read_bytes())
        merged.import_pages(source)
        source.close()
    buffer = io.BytesIO()
    merged.save(buffer)
    merged.close()
    return buffer.getvalue()


@pytest.fixture(scope="module")
def cut(tender: bytes) -> dict[int, bytes]:
    return pdf_parsing.split_pages(tender, [0, 1])


@pytest.mark.parametrize("index", [0, 1])
def test_a_cut_page_has_the_geometry_it_had_in_the_document(
    tender: bytes, cut: dict[int, bytes], index: int
) -> None:
    whole = geometry.from_parquet(geometry_pdf.extract(tender, index)["parquet"])
    alone = geometry.from_parquet(geometry_pdf.extract(cut[index], 0)["parquet"])

    assert alone.num_rows == whole.num_rows > 0
    assert geometry.counts(alone) == geometry.counts(whole)
    for column in ("kind", "color", "text"):
        assert alone.column(column).to_pylist() == whole.column(column).to_pylist()
    for column in ("minx", "miny", "maxx", "maxy"):
        assert alone.column(column).to_pylist() == pytest.approx(
            whole.column(column).to_pylist(), abs=1e-6, nan_ok=True
        )


@pytest.mark.parametrize("index", [0, 1])
def test_a_cut_page_has_the_text_and_the_pixels_it_had(
    tender: bytes, cut: dict[int, bytes], index: int
) -> None:
    assert text_parsing.pdf_text(cut[index], 0) == text_parsing.pdf_text(tender, index)
    assert pdf_parsing.render_page(cut[index], 0, 600, 424) == pdf_parsing.render_page(
        tender, index, 600, 424
    )


def test_the_two_sheets_are_cut_apart(tender: bytes, cut: dict[int, bytes]) -> None:
    assert len(pdf_parsing.inspect_pdf(cut[0])) == 1
    assert geometry_pdf.extract(cut[0], 0)["parquet"] != geometry_pdf.extract(cut[1], 0)["parquet"]
    # Each page is smaller than the set it came from.
    assert all(len(page) < len(tender) for page in cut.values())


def test_a_page_the_document_does_not_have_is_refused(tender: bytes) -> None:
    with pytest.raises(pdf_parsing.PdfUnreadable, match="no page 3"):
        pdf_parsing.split_pages(tender, [2])


# --- Storing and fetching a sheet's page -------------------------------------------------------


def document_of(kind: str = "pdf") -> Document:
    return Document(
        id=uuid.uuid4(),
        bid_id=uuid.uuid4(),
        filename="tender.pdf",
        media_type="application/pdf",
        kind=kind,
        sha256="a" * 64,
        byte_size=1,
        storage_key="bids/x/documents/tender.pdf",
    )


def sheet_of(document: Document, index: int) -> Sheet:
    return Sheet(bid_id=document.bid_id, document_id=document.id, index_in_document=index)


def test_a_sheet_fetches_its_own_page_and_reads_it_as_page_one(tender: bytes) -> None:
    store = MemoryObjectStore()
    document = document_of()
    store.put_once(document.storage_key, tender, content_type="application/pdf")

    assert pages.store_pages(store, document, tender, 2) == 2

    sheet = sheet_of(document, 1)
    payload = pages.sheet_payload(store, document, sheet)
    assert isinstance(payload, PagePayload)
    assert len(payload) < len(tender)
    assert pages.page_index(sheet, payload) == 0
    # The same sheet from the whole document is read at its own index.
    assert pages.page_index(sheet, tender) == 1
    assert text_parsing.pdf_text(payload, 0) == text_parsing.pdf_text(tender, 1)


def test_cutting_again_writes_nothing(tender: bytes) -> None:
    store = MemoryObjectStore()
    document = document_of()
    pages.store_pages(store, document, tender, 2)
    writes = store.writes

    assert pages.store_pages(store, document, tender, 2) == 2
    assert store.writes == writes


def test_with_no_page_file_the_whole_document_is_used(tender: bytes) -> None:
    store = MemoryObjectStore()
    document = document_of()
    store.put_once(document.storage_key, tender, content_type="application/pdf")
    sheet = sheet_of(document, 1)

    payload = pages.sheet_payload(store, document, sheet)

    assert not isinstance(payload, PagePayload) and payload == tender
    assert pages.page_index(sheet, payload) == 1


def test_a_one_page_pdf_and_a_dxf_are_not_cut(tender: bytes) -> None:
    store = MemoryObjectStore()

    assert pages.store_pages(store, document_of(), tender, 1) == 0
    assert pages.store_pages(store, document_of("dxf"), b"0\nSECTION\n", 4) == 0
    assert store.writes == 0


def test_a_pdf_that_cannot_be_cut_is_read_whole() -> None:
    store = MemoryObjectStore()
    document = document_of()

    assert pages.store_pages(store, document, b"not a pdf at all", 3) == 0
    assert store.writes == 0
