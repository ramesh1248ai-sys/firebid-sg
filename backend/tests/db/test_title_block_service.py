"""Each sheet's title block becomes a sheet revision proposal (FR-DOC-02).

Deterministic reading happens in the sandbox beside the parse job; the model is asked only
about what that could not read, and only on a crop; a person is asked when neither is sure.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from firebid.ai_gateway.config import load_config
from firebid.ai_gateway.providers.fake import FakeAdapter
from firebid.ai_gateway.router import Router
from firebid.db.models.core import Bid
from firebid.db.models.documents import Document, Sheet, SheetRevision, TitleBlockLayout
from firebid.db.models.workflow import HumanTask
from firebid.drawings.title_block import Box, Span, fingerprint, learn, read
from firebid.evals import synthetic
from firebid.ingest.scanning import AlwaysCleanScanner
from firebid.parsing.text import dxf_text
from firebid.sandbox.runner import SandboxFailure
from firebid.services import title_blocks
from firebid.services.ingestion import Ingestor
from firebid.services.sheets import process_document
from firebid.services.title_blocks import check_with_model, read_title_blocks
from firebid.storage.object_store import MemoryObjectStore

pytestmark = pytest.mark.req("FR-DOC-02")

CONFIG = """
version: 1
providers:
  primary:
    kind: fake
    approved_data_classes: [internal, confidential]
models:
  first:
    provider: primary
    model_id: first-1
    capabilities: [vision, structured_output]
routes:
  title_block_read:
    requires: [vision, structured_output]
    data_class: confidential
    models: [first]
    reasoning: low
"""


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


@pytest.fixture
def router(tmp_path: Path) -> Callable[..., Router]:
    path = tmp_path / "llm.yaml"
    path.write_text(CONFIG, encoding="utf-8")

    def build(*replies: dict[str, Any]) -> Router:
        adapter = FakeAdapter("primary")
        for reply in replies:
            adapter.reply(json.dumps(reply))
        return Router(
            config=load_config(path),
            adapters={"primary": adapter},
            backoff_base_seconds=0,
            sleep=lambda _s: None,
        )

    return build


def ingest(
    session: Session, bid: Bid, store: MemoryObjectStore, name: str, payload: bytes
) -> tuple[Document, list[Sheet]]:
    outcome = Ingestor(session, store, AlwaysCleanScanner(), bid_id=bid.id).ingest(name, payload)
    assert outcome.stored, outcome.rejected
    document = outcome.stored[0]
    sheets = process_document(session, store, document).sheets
    return document, sheets


def live_pdf(tmp_path: Path, **changes: Any) -> bytes:
    document, _ = synthetic.general_arrangement(**changes)
    return synthetic.write_pdf(document, tmp_path / "live.pdf", live_text=True).read_bytes()


def outlined_pdf(tmp_path: Path) -> bytes:
    document, _ = synthetic.general_arrangement()
    return synthetic.write_pdf(document, tmp_path / "outlined.pdf").read_bytes()


def dxf(**changes: Any) -> bytes:
    document, _ = synthetic.general_arrangement(**changes)
    return synthetic.dxf_bytes(document)


def checks_queued_for(session: Session, revision: SheetRevision) -> list[dict[str, Any]]:
    """Model checks queued for one revision. The job table outlives each test's cleanup."""
    rows = session.execute(
        text(
            "SELECT args FROM procrastinate_jobs WHERE task_name = 'title_block.check' "
            "AND args->>'revision_id' = :revision ORDER BY id"
        ),
        {"revision": str(revision.id)},
    )
    return [dict(args) for (args,) in rows]


def reading_of(revision: SheetRevision) -> dict[str, Any]:
    assert revision.reading is not None
    return cast(dict[str, Any], revision.reading)


class TestReadingFromTheFile:
    def test_a_pdf_text_layer_becomes_a_confident_proposal(
        self, session: Session, bid: Bid, store: MemoryObjectStore, tmp_path: Path
    ) -> None:
        document, sheets = ingest(session, bid, store, "FP-L05-201.pdf", live_pdf(tmp_path))

        [revision] = read_title_blocks(session, store, document, sheets)

        assert (revision.sheet_number, revision.revision_label) == ("FP-L05-201", "R04")
        # Confident and uncontested, so the platform registered it and, being the only
        # revision of its drawing, made it Current. The Estimator's register confirmation
        # is the person's decision on the set (FR-DOC-03).
        assert revision.state == "current"
        assert revision.extraction_method == "text_layer"
        assert revision.source_confidence == pytest.approx(0.97)
        assert revision.title == "FIRE SPRINKLER LAYOUT"
        assert revision.scale_text == "1:100"
        assert revision.level == "L05"
        assert revision.discipline == "fire protection"
        assert revision.revision_date is not None
        assert revision.reading is not None
        assert reading_of(revision)["fields"]["revision"]["how"] == "label"
        assert revision.sources == {"title_block": "R04", "filename": None, "transmittal": None}
        assert "crop_key" not in reading_of(revision), "nothing to ask the model"

    def test_a_dxf_is_read_from_its_entities(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        document, sheets = ingest(session, bid, store, "FP-L05-201.dxf", dxf())

        [revision] = read_title_blocks(session, store, document, sheets)

        assert (revision.sheet_number, revision.revision_label) == ("FP-L05-201", "R04")
        assert revision.extraction_method == "cad"

    def test_reading_twice_changes_nothing(
        self, session: Session, bid: Bid, store: MemoryObjectStore, tmp_path: Path
    ) -> None:
        document, sheets = ingest(session, bid, store, "FP-L05-201.pdf", live_pdf(tmp_path))

        first = read_title_blocks(session, store, document, sheets)
        again = read_title_blocks(session, store, document, sheets)

        assert [revision.id for revision in first] == [revision.id for revision in again]
        assert session.execute(select(SheetRevision)).scalars().all() == first

    def test_the_same_drawing_in_two_formats_is_one_revision(
        self, session: Session, bid: Bid, store: MemoryObjectStore, tmp_path: Path
    ) -> None:
        cad_document, cad_sheets = ingest(session, bid, store, "FP-L05-201.dxf", dxf())
        pdf_document, pdf_sheets = ingest(session, bid, store, "FP-L05-201.pdf", live_pdf(tmp_path))

        [from_cad] = read_title_blocks(session, store, cad_document, cad_sheets)
        [from_pdf] = read_title_blocks(session, store, pdf_document, pdf_sheets)

        assert from_pdf.id == from_cad.id
        assert from_cad.alternate_sheet_ids == [str(pdf_sheets[0].id)]
        assert len(session.execute(select(SheetRevision)).scalars().all()) == 1


class TestWhenTheReadingIsUnsure:
    def test_an_outlined_pdf_is_sent_for_a_model_check_with_a_crop(
        self, session: Session, bid: Bid, store: MemoryObjectStore, tmp_path: Path
    ) -> None:
        """No text layer. OCR, where installed, is not sure enough; the model gets a crop."""
        document, sheets = ingest(session, bid, store, "scan.pdf", outlined_pdf(tmp_path))

        [revision] = read_title_blocks(session, store, document, sheets)

        key = reading_of(revision)["crop_key"]
        assert store.get(key).startswith(b"\x89PNG"), "the model is shown a PNG, not the file"
        assert checks_queued_for(session, revision) == [
            {"revision_id": str(revision.id), "user_id": ""}
        ]

    def test_an_unclear_dxf_goes_straight_to_a_person(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        """CAD text is exact. A model cannot read an entity better, so a person decides."""
        document_dxf, space = synthetic._new_drawing()
        space.add_text("FIRE SPRINKLER LAYOUT", height=250).set_placement((1_000, 1_000))
        document, sheets = ingest(
            session, bid, store, "untitled.dxf", synthetic.dxf_bytes(document_dxf)
        )

        [revision] = read_title_blocks(session, store, document, sheets)

        assert revision.sheet_number is None, "absent beats invented"
        task = session.execute(select(HumanTask)).scalar_one()
        assert task.kind == "title_block_review"
        assert task.payload["sheet_revision_id"] == str(revision.id)
        assert checks_queued_for(session, revision) == []

    def test_a_sheet_that_cannot_be_read_still_appears_for_a_person(
        self,
        session: Session,
        bid: Bid,
        store: MemoryObjectStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        document, sheets = ingest(session, bid, store, "FP-L05-201.pdf", live_pdf(tmp_path))

        def fail(*_args: Any, **_kwargs: Any) -> Any:
            raise SandboxFailure("crashed", "the parser stopped unexpectedly")

        monkeypatch.setattr(title_blocks, "run_sandboxed", fail)
        [revision] = read_title_blocks(session, store, document, sheets)

        assert revision.sheet_number is None
        assert revision.reading is not None
        assert "stopped unexpectedly" in reading_of(revision)["error"]
        assert session.execute(select(HumanTask)).scalar_one().kind == "title_block_review"


class TestRememberedLayouts:
    def test_a_known_consultant_layout_is_used(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        learnt_from = dxf_text(dxf(sheet_number="FP-L05-201"), None)
        page = Box(*learnt_from["page"])
        spans = [Span(*item) for item in learnt_from["spans"]]
        layout = learn(read(spans, page), page)
        session.add(
            TitleBlockLayout(
                organisation_id=bid.organisation_id,
                consultant="Synthetic Consultants",
                fingerprint=[list(mark) for mark in fingerprint(spans, page)],
                layout=layout.to_json(),
            )
        )
        session.flush()
        document, sheets = ingest(
            session, bid, store, "FP-L06-201.dxf", dxf(sheet_number="FP-L06-201", revision="R02")
        )

        [revision] = read_title_blocks(session, store, document, sheets)

        assert (revision.sheet_number, revision.revision_label) == ("FP-L06-201", "R02")
        assert revision.extraction_method == "layout"
        remembered = session.execute(select(TitleBlockLayout)).scalar_one()
        assert remembered.times_used == 1


class TestTheModelCheck:
    def unsure_revision(
        self, session: Session, bid: Bid, store: MemoryObjectStore, tmp_path: Path
    ) -> SheetRevision:
        document, sheets = ingest(session, bid, store, "scan.pdf", outlined_pdf(tmp_path))
        [revision] = read_title_blocks(session, store, document, sheets)
        return revision

    def test_a_confident_model_reading_fills_the_proposal(
        self,
        session: Session,
        bid: Bid,
        store: MemoryObjectStore,
        tmp_path: Path,
        router: Callable[..., Router],
    ) -> None:
        revision = self.unsure_revision(session, bid, store, tmp_path)
        reply = {
            "sheet_number": "FP-L05-201",
            "sheet_number_confidence": 0.98,
            "revision": "R04",
            "revision_confidence": 0.97,
            "sheet_title": "FIRE SPRINKLER LAYOUT",
            "sheet_title_confidence": 0.9,
        }

        check_with_model(session, store, revision, router(reply))

        assert (revision.sheet_number, revision.revision_label) == ("FP-L05-201", "R04")
        assert revision.extraction_method == "model"
        # Confident once the model read it, so it is settled like any confident reading.
        assert revision.state == "current"
        assert revision.reading is not None
        assert reading_of(revision)["fields"]["sheet_number"]["how"] == "model"
        assert reading_of(revision)["fields"]["sheet_number"]["agent_run_id"]

    def test_an_unsure_model_hands_the_sheet_to_a_person(
        self,
        session: Session,
        bid: Bid,
        store: MemoryObjectStore,
        tmp_path: Path,
        router: Callable[..., Router],
    ) -> None:
        revision = self.unsure_revision(session, bid, store, tmp_path)
        reply = {"sheet_number": "FP-L05-201", "sheet_number_confidence": 0.4}

        check_with_model(session, store, revision, router(reply))

        assert revision.reading is not None
        task = session.get(HumanTask, reading_of(revision)["review_task_id"])
        assert task is not None
        assert task.state == "open"
