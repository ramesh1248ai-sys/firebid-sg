"""Each sheet's geometry: extracted in the sandbox, cached by content, indexed by place."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from firebid.db.models.core import Bid
from firebid.db.models.documents import Document, Sheet
from firebid.db.models.drawings import GeometryFeature, SheetGeometry
from firebid.drawings import geometry
from firebid.evals import synthetic
from firebid.ingest.scanning import AlwaysCleanScanner
from firebid.services import geometry as geometry_service
from firebid.services.geometry import extract_sheet, load
from firebid.services.ingestion import Ingestor
from firebid.services.sheets import process_document
from firebid.storage.object_store import MemoryObjectStore

pytestmark = pytest.mark.req("FR-VIS-01")


@pytest.fixture(autouse=True)
def no_tiles(monkeypatch: pytest.MonkeyPatch) -> None:
    from firebid.services import sheets

    monkeypatch.setattr(sheets, "_render_low_levels", lambda *_args, **_kwargs: 0)


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


@pytest.fixture
def parses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every extraction that reaches the sandbox, by function name."""
    calls: list[str] = []
    from firebid.sandbox.runner import run_sandboxed as real

    def counting(function: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(function.__name__)
        return real(function, *args, **kwargs)

    monkeypatch.setattr(geometry_service, "run_sandboxed", counting)
    return calls


PLAN = synthetic.dxf_bytes(synthetic.general_arrangement()[0])  # one set of bytes, reused


def sheet_of(session: Session, bid: Bid, store: MemoryObjectStore) -> tuple[Document, Sheet]:
    document = (
        Ingestor(session, store, AlwaysCleanScanner(), bid_id=bid.id)
        .ingest("FP-L05-201.dxf", PLAN)
        .stored[0]
    )
    [sheet] = process_document(session, store, document).sheets
    return document, sheet


class TestExtraction:
    def test_a_sheets_geometry_is_recorded_and_stored(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        document, sheet = sheet_of(session, bid, store)

        record = extract_sheet(session, store, document, sheet, None)

        assert record.method == "cad_entity"
        assert record.page == [0.0, 0.0, 420.0, 297.0]
        assert record.counts["circle"] == 48 + 9, "48 sprinklers and 9 grid bubbles"
        assert record.views and record.views[0]["denominator"] == 100.0
        assert not record.from_cache
        table = load(store, record)
        assert table.num_rows == sum(record.counts.values())

    def test_findable_primitives_are_indexed_by_place(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        document, sheet = sheet_of(session, bid, store)
        extract_sheet(session, store, document, sheet, None)

        # What is in the bottom-right corner, where the title block is?
        found = (
            session.execute(
                text(
                    "SELECT label FROM geometry_feature WHERE sheet_id = :sheet "
                    "AND kind = 'text' AND bbox && box(point(275, 250), point(420, 297))"
                ),
                {"sheet": sheet.id},
            )
            .scalars()
            .all()
        )

        assert "FP-L05-201" in found
        assert "DN150 RISING MAIN" not in found, "that label is on the plan, far from the corner"
        feature = session.execute(
            select(GeometryFeature).where(GeometryFeature.label == "FP-L05-201")
        ).scalar_one()
        assert feature.bbox[0] > 275


class TestTheStageCache:
    def test_an_unchanged_sheet_is_a_cache_hit_with_no_parsing(
        self,
        session: Session,
        bid: Bid,
        second_bid: Bid,
        store: MemoryObjectStore,
        parses: list[str],
    ) -> None:
        """The same drawing on another bid: its geometry is loaded, not extracted again."""
        document, sheet = sheet_of(session, bid, store)
        extract_sheet(session, store, document, sheet, None)
        assert parses == ["extract"]

        other_document, other_sheet = sheet_of(session, second_bid, store)
        assert other_sheet.content_hash == sheet.content_hash
        record = extract_sheet(session, store, other_document, other_sheet, None)

        assert parses == ["extract"], "no second parse"
        assert record.from_cache
        assert record.counts["circle"] == 57
        assert record.bid_id == second_bid.id

    def test_the_same_sheet_again_changes_nothing(
        self, session: Session, bid: Bid, store: MemoryObjectStore, parses: list[str]
    ) -> None:
        document, sheet = sheet_of(session, bid, store)
        first = extract_sheet(session, store, document, sheet, None)

        again = extract_sheet(session, store, document, sheet, None)

        assert again.id == first.id
        assert parses == ["extract"]

    def test_a_new_extractor_version_extracts_again(
        self,
        session: Session,
        bid: Bid,
        store: MemoryObjectStore,
        parses: list[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        document, sheet = sheet_of(session, bid, store)
        before = extract_sheet(session, store, document, sheet, None)

        monkeypatch.setattr(geometry, "EXTRACTOR_VERSION", "2-test")
        after = extract_sheet(session, store, document, sheet, None)

        assert parses == ["extract", "extract"]
        assert after.extractor_version == "2-test"
        assert after.object_key != before.object_key or after.id == before.id
        assert "/v2-test/" in after.object_key
        assert session.execute(select(SheetGeometry)).scalars().all() == [after]
