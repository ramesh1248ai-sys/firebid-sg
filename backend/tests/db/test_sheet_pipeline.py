"""A document becoming sheets with tiles (FR-DOC-01, FR-DOC-07, NFR-06).

The whole path, against a real database and real files: upload bytes, process them, and
check that each page became a sheet with its size, its content class, its lineage and the
tiles a viewer will ask for.

The sandbox is used for real here. That makes these tests slower than mocking it, and it is
the point: the parsers must work through a process boundary, with their arguments and results
pickled, which is exactly where a change breaks them.
"""

from __future__ import annotations

import io
from typing import Any, cast

import pytest
from PIL import Image
from procrastinate import JobContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.models.core import AppUser, Bid
from firebid.db.models.documents import Document, Sheet
from firebid.evals.synthetic import dxf_bytes, general_arrangement, write_pdf, write_raster
from firebid.imaging.pyramid import Pyramid, thumbnail_key, tile_key
from firebid.ingest.scanning import AlwaysCleanScanner
from firebid.jobs.tasks import parse_document
from firebid.services.ingestion import Ingestor
from firebid.services.sheets import process_document, render_tile
from firebid.storage import object_store
from firebid.storage.object_store import MemoryObjectStore

pytestmark = pytest.mark.req("FR-DOC-01")


@pytest.fixture(scope="module")
def drawing() -> Any:
    document, _ = general_arrangement(columns=6, rows=4)
    return document


@pytest.fixture(scope="module")
def vector_pdf(drawing: Any, tmp_path_factory: pytest.TempPathFactory) -> bytes:
    return write_pdf(drawing, tmp_path_factory.mktemp("pdf") / "ga.pdf").read_bytes()


@pytest.fixture(scope="module")
def scanned_pdf(drawing: Any, tmp_path_factory: pytest.TempPathFactory) -> bytes:
    out = tmp_path_factory.mktemp("scan")
    png = write_raster(drawing, out / "ga.png", dpi=72)
    target = out / "scan.pdf"
    with Image.open(png) as image:
        image.convert("RGB").save(target, "PDF", resolution=72.0)
    return target.read_bytes()


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


def ingest(
    session: Session, bid: Bid, store: MemoryObjectStore, name: str, payload: bytes
) -> Document:
    outcome = Ingestor(session, store, AlwaysCleanScanner(), bid_id=bid.id).ingest(name, payload)
    assert outcome.stored, outcome.rejected
    return outcome.stored[0]


class TestProcessingAPdf:
    def test_each_page_becomes_a_sheet_with_its_paper_size(
        self, session: Session, bid: Bid, store: MemoryObjectStore, vector_pdf: bytes
    ) -> None:
        document = ingest(session, bid, store, "FP-L05-201.pdf", vector_pdf)

        outcome = process_document(session, store, document)

        assert outcome.failure is None
        assert len(outcome.sheets) == 1
        sheet = outcome.sheets[0]
        assert sheet.width_mm and sheet.width_mm > 100
        assert sheet.height_mm and sheet.height_mm > 50
        assert sheet.index_in_document == 0
        assert document.state == "done"

    def test_a_sheet_carries_its_lineage(
        self, session: Session, bid: Bid, store: MemoryObjectStore, vector_pdf: bytes
    ) -> None:
        """FR-DOC-07: every quantity measured on this sheet inherits this reference."""
        document = ingest(session, bid, store, "FP-L05-201.pdf", vector_pdf)

        sheet = process_document(session, store, document).sheets[0]

        assert sheet.source_ref is not None
        assert sheet.source_ref["document_id"] == str(document.id)
        assert sheet.source_ref["page_or_layout"] == "page 1"

    def test_a_vector_page_is_recorded_as_vector(
        self, session: Session, bid: Bid, store: MemoryObjectStore, vector_pdf: bytes
    ) -> None:
        document = ingest(session, bid, store, "FP-L05-201.pdf", vector_pdf)

        sheet = process_document(session, store, document).sheets[0]

        assert sheet.content_class == "vector"
        assert sheet.quality_detail is not None
        path_objects = sheet.quality_detail["path_objects"]
        assert isinstance(path_objects, int) and path_objects > 0

    def test_a_scanned_page_is_recorded_as_raster(
        self, session: Session, bid: Bid, store: MemoryObjectStore, scanned_pdf: bytes
    ) -> None:
        document = ingest(session, bid, store, "scan.pdf", scanned_pdf)

        sheet = process_document(session, store, document).sheets[0]

        assert sheet.content_class == "raster"

    def test_the_low_levels_and_a_thumbnail_are_stored(
        self, session: Session, bid: Bid, store: MemoryObjectStore, vector_pdf: bytes
    ) -> None:
        document = ingest(session, bid, store, "FP-L05-201.pdf", vector_pdf)

        outcome = process_document(session, store, document)
        sheet = outcome.sheets[0]

        assert sheet.content_hash is not None
        pyramid = Pyramid(width_px=sheet.base_width_px or 0, height_px=sheet.base_height_px or 0)
        for level in pyramid.pre_rendered_levels:
            key = tile_key(sheet.content_hash, level, 0, 0)
            assert store.exists(key), f"level {level} was not pre-rendered"
        assert store.exists(thumbnail_key(sheet.content_hash))
        assert outcome.tiles_written > 0

    def test_the_close_up_levels_are_left_for_later(
        self, session: Session, bid: Bid, store: MemoryObjectStore, vector_pdf: bytes
    ) -> None:
        """Rendering every level at ingest would spend most of it on tiles nobody opens."""
        document = ingest(session, bid, store, "FP-L05-201.pdf", vector_pdf)

        sheet = process_document(session, store, document).sheets[0]
        pyramid = Pyramid(width_px=sheet.base_width_px or 0, height_px=sheet.base_height_px or 0)

        assert pyramid.on_demand_levels, "this fixture should be big enough to have some"
        for level in pyramid.on_demand_levels:
            assert not store.exists(tile_key(sheet.content_hash or "", level, 0, 0))

    def test_a_stored_tile_is_a_readable_image(
        self, session: Session, bid: Bid, store: MemoryObjectStore, vector_pdf: bytes
    ) -> None:
        document = ingest(session, bid, store, "FP-L05-201.pdf", vector_pdf)
        sheet = process_document(session, store, document).sheets[0]

        payload = store.get(thumbnail_key(sheet.content_hash or ""))

        with Image.open(io.BytesIO(payload)) as image:
            assert image.format == "WEBP"
            assert max(image.size) <= 400


class TestProcessingADxf:
    def test_a_layout_becomes_a_sheet(
        self, session: Session, bid: Bid, store: MemoryObjectStore, drawing: Any
    ) -> None:
        document = ingest(session, bid, store, "FP-L05-201.dxf", dxf_bytes(drawing))

        outcome = process_document(session, store, document)

        assert outcome.failure is None
        assert len(outcome.sheets) >= 1
        sheet = outcome.sheets[0]
        assert sheet.content_class == "vector"
        assert sheet.width_mm and sheet.width_mm > 100
        assert sheet.layout_name

    def test_its_tiles_are_rendered_too(
        self, session: Session, bid: Bid, store: MemoryObjectStore, drawing: Any
    ) -> None:
        document = ingest(session, bid, store, "FP-L05-201.dxf", dxf_bytes(drawing))

        sheet = process_document(session, store, document).sheets[0]

        assert store.exists(thumbnail_key(sheet.content_hash or ""))


class TestReRunning:
    def test_processing_the_same_document_twice_changes_nothing(
        self, session: Session, bid: Bid, store: MemoryObjectStore, vector_pdf: bytes
    ) -> None:
        """Job delivery is at-least-once, so this has to hold."""
        document = ingest(session, bid, store, "FP-L05-201.pdf", vector_pdf)

        first = process_document(session, store, document)
        objects_after_first = dict(store.objects)
        second = process_document(session, store, document)

        assert [sheet.id for sheet in second.sheets] == [sheet.id for sheet in first.sheets]
        assert store.objects == objects_after_first
        assert second.tiles_written == 0, "nothing was re-rendered"
        assert len(session.execute(select(Sheet)).scalars().all()) == len(first.sheets)

    def test_the_same_drawing_on_another_bid_reuses_its_tiles(
        self,
        session: Session,
        bid: Bid,
        second_bid: Bid,
        store: MemoryObjectStore,
        vector_pdf: bytes,
    ) -> None:
        """Two clients sending the same consultant's drawing render it once."""
        first = ingest(session, bid, store, "FP-L05-201.pdf", vector_pdf)
        process_document(session, store, first)
        writes_after_first = store.writes

        second = ingest(session, second_bid, store, "FP-L05-201.pdf", vector_pdf)
        outcome = process_document(session, store, second)

        assert outcome.tiles_written == 0
        # The second bid still gets its own sheet row; only the pixels are shared.
        assert outcome.sheets[0].bid_id == second_bid.id
        assert store.writes == writes_after_first + 1, "only the document itself was written"


class TestWhatItRefuses:
    def test_a_damaged_pdf_leaves_the_document_rejected_with_a_reason(
        self, session: Session, bid: Bid, store: MemoryObjectStore, vector_pdf: bytes
    ) -> None:
        document = ingest(session, bid, store, "broken.pdf", vector_pdf[:900] + b"\n%%EOF\n")

        outcome = process_document(session, store, document)

        assert outcome.sheets == []
        assert document.state == "rejected"
        assert document.rejected_reason
        assert "damaged" in document.rejected_reason or "could not" in document.rejected_reason

    def test_a_quarantined_file_is_never_opened(
        self, session: Session, bid: Bid, store: MemoryObjectStore, vector_pdf: bytes
    ) -> None:
        """Guardrail 9, at the parsing end: a scan result is not advice."""
        document = ingest(session, bid, store, "FP-L05-201.pdf", vector_pdf)
        document.state = "quarantined"
        session.flush()

        outcome = process_document(session, store, document)

        assert outcome.sheets == []
        assert "quarantined" in (outcome.failure or "")
        assert document.state == "quarantined", "processing did not move it on"

    def test_an_unscanned_file_is_never_opened(
        self, session: Session, bid: Bid, store: MemoryObjectStore, vector_pdf: bytes
    ) -> None:
        document = ingest(session, bid, store, "FP-L05-201.pdf", vector_pdf)
        document.scanned_at = None
        session.flush()

        outcome = process_document(session, store, document)

        assert outcome.sheets == []
        assert "scanned" in (outcome.failure or "")

    def test_a_file_held_by_an_outage_is_never_opened(
        self, session: Session, bid: Bid, store: MemoryObjectStore, vector_pdf: bytes
    ) -> None:
        document = ingest(session, bid, store, "FP-L05-201.pdf", vector_pdf)
        document.state = "awaiting_scan"
        session.flush()

        outcome = process_document(session, store, document)

        assert outcome.sheets == []
        assert document.state == "awaiting_scan"

    def test_a_spreadsheet_does_not_become_sheets(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        """XLSX is registered at ingest; classification is P1-02's job, not this one's."""
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("[Content_Types].xml", b"<Types/>")
            archive.writestr("xl/workbook.xml", b"<xml/>")
        document = ingest(session, bid, store, "boq.xlsx", buffer.getvalue())

        outcome = process_document(session, store, document)

        assert outcome.sheets == []
        assert "do not become sheets" in (outcome.failure or "")


class TestOnDemandTiles:
    def test_a_close_up_tile_is_rendered_on_first_request_and_then_cached(
        self, session: Session, bid: Bid, store: MemoryObjectStore, vector_pdf: bytes
    ) -> None:
        document = ingest(session, bid, store, "FP-L05-201.pdf", vector_pdf)
        sheet = process_document(session, store, document).sheets[0]
        pyramid = Pyramid(width_px=sheet.base_width_px or 0, height_px=sheet.base_height_px or 0)
        level = pyramid.on_demand_levels[0]

        payload = render_tile(store, vector_pdf, "pdf", sheet, level, 0, 0)
        writes_after_render = store.writes

        assert store.exists(tile_key(sheet.content_hash or "", level, 0, 0))
        with Image.open(io.BytesIO(payload)) as tile:
            assert tile.format == "WEBP"

        again = render_tile(store, vector_pdf, "pdf", sheet, level, 0, 0)

        assert again == payload
        assert store.writes == writes_after_render, "a cached tile is not re-rendered"

    def test_a_pre_rendered_tile_comes_from_the_cache(
        self, session: Session, bid: Bid, store: MemoryObjectStore, vector_pdf: bytes
    ) -> None:
        document = ingest(session, bid, store, "FP-L05-201.pdf", vector_pdf)
        sheet = process_document(session, store, document).sheets[0]
        writes_before = store.writes

        render_tile(store, vector_pdf, "pdf", sheet, 0, 0, 0)

        assert store.writes == writes_before


class TestTheParseJob:
    """The job as the sandbox pool runs it: on the application role, under row-level security.

    The tests above use the table owner, which row-level security does not restrict, so they
    cannot see a job that forgets whose documents it is reading.
    """

    @pytest.fixture(autouse=True)
    def the_real_store(self, store: MemoryObjectStore, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(object_store, "get_object_store", lambda: store)

    def test_it_reads_the_uploaders_document(
        self,
        session: Session,
        bid: Bid,
        user: AppUser,
        store: MemoryObjectStore,
        vector_pdf: bytes,
        as_application_role: None,
    ) -> None:
        document = ingest(session, bid, store, "FP-L05-201.pdf", vector_pdf)
        session.commit()

        result = parse_document(
            cast(JobContext, None), document_id=str(document.id), user_id=str(user.id)
        )

        assert result["sheets"] == 1, "a job with no acting user sees no documents at all"
        session.expire_all()
        sheets = session.execute(select(Sheet).where(Sheet.document_id == document.id)).all()
        assert len(sheets) == 1
