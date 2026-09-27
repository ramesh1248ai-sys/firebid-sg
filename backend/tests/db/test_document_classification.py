"""Each document's type: proposed in the parse job, settled by a model or a person (FR-DOC-02)."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import openpyxl
import pytest
from procrastinate import JobContext
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from firebid.ai_gateway.config import load_config
from firebid.ai_gateway.providers.fake import FakeAdapter
from firebid.ai_gateway.router import Router
from firebid.api.progress import read_progress
from firebid.db.models.core import AppUser, Bid
from firebid.db.models.documents import Document
from firebid.db.models.workflow import HumanTask
from firebid.evals import synthetic
from firebid.ingest.scanning import AlwaysCleanScanner
from firebid.jobs.tasks import parse_document
from firebid.sandbox.office import Converted
from firebid.services.classification import classify_in_sandbox, classify_with_model
from firebid.services.ingestion import Ingestor
from firebid.storage import object_store
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
    capabilities: [structured_output]
routes:
  doc_classify:
    requires: [structured_output]
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

    def build(reply: dict[str, Any]) -> Router:
        adapter = FakeAdapter("primary").reply(json.dumps(reply))
        return Router(
            config=load_config(path),
            adapters={"primary": adapter},
            backoff_base_seconds=0,
            sleep=lambda _s: None,
        )

    return build


def workbook(*rows: list[object]) -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


BOQ = workbook(
    ["BILL NO. 3 - FIRE PROTECTION SERVICES"],
    ["ITEM", "DESCRIPTION", "QTY", "UNIT", "RATE", "AMOUNT"],
    ["A", "Pendent sprinkler, K80", 412, "no", None, None],
)
UNCLEAR = workbook(["Site record"], ["Photo", "Taken", "By"], ["IMG_001", "2026-05-01", "RL"])


def ingest(
    session: Session, bid: Bid, store: MemoryObjectStore, name: str, payload: bytes
) -> Document:
    outcome = Ingestor(session, store, AlwaysCleanScanner(), bid_id=bid.id).ingest(name, payload)
    assert outcome.stored, outcome.rejected
    return outcome.stored[0]


def model_checks_for(session: Session, document: Document) -> int:
    """Model checks queued for one document. The job table outlives each test's cleanup."""
    return int(
        session.execute(
            text(
                "SELECT count(*) FROM procrastinate_jobs WHERE task_name = 'document.classify' "
                "AND args->>'document_id' = :document"
            ),
            {"document": str(document.id)},
        ).scalar_one()
    )


def kept(document: Document) -> dict[str, Any]:
    assert document.classification is not None
    return cast(dict[str, Any], document.classification)


class TestInTheSandbox:
    def test_a_boq_is_classified_by_the_rules_alone(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        document = ingest(session, bid, store, "Tender BOQ.xlsx", BOQ)

        classify_in_sandbox(session, document, BOQ)

        assert document.doc_type == "boq"
        assert document.doc_type_confidence is not None and document.doc_type_confidence >= 0.8
        assert kept(document)["method"] == "rules"
        assert kept(document)["digest"]["header_rows"][0][:2] == ["ITEM", "DESCRIPTION"]
        assert model_checks_for(session, document) == 0

    def test_an_unclear_file_is_sent_to_the_model(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        document = ingest(session, bid, store, "records.xlsx", UNCLEAR)

        classify_in_sandbox(session, document, UNCLEAR)

        assert document.doc_type == "other"
        assert model_checks_for(session, document) == 1

    def test_a_drawing_pdf_is_a_drawing_by_its_title_blocks(
        self, session: Session, bid: Bid, store: MemoryObjectStore, tmp_path: Path
    ) -> None:
        from firebid.services.sheets import process_document
        from firebid.services.title_blocks import read_title_blocks

        drawing, _ = synthetic.general_arrangement()
        payload = synthetic.write_pdf(drawing, tmp_path / "ga.pdf", live_text=True).read_bytes()
        document = ingest(session, bid, store, "FP-L05-201.pdf", payload)
        read_title_blocks(
            session, store, document, process_document(session, store, document).sheets
        )

        classify_in_sandbox(session, document, payload)

        assert document.doc_type == "drawing"


class TestTheModel:
    def test_a_confident_answer_is_recorded_as_the_models(
        self,
        session: Session,
        bid: Bid,
        store: MemoryObjectStore,
        router: Callable[..., Router],
    ) -> None:
        document = ingest(session, bid, store, "records.xlsx", UNCLEAR)
        classify_in_sandbox(session, document, UNCLEAR)

        classify_with_model(
            session,
            document,
            router({"doc_type": "other", "confidence": 0.93, "reason": "a photo record"}),
        )

        assert document.doc_type == "other"
        assert kept(document)["method"] == "model"
        assert kept(document)["agent_run_id"]

    def test_an_unsure_answer_goes_to_a_person(
        self,
        session: Session,
        bid: Bid,
        store: MemoryObjectStore,
        router: Callable[..., Router],
    ) -> None:
        document = ingest(session, bid, store, "records.xlsx", UNCLEAR)
        classify_in_sandbox(session, document, UNCLEAR)

        classify_with_model(
            session, document, router({"doc_type": "schedule", "confidence": 0.4, "reason": "?"})
        )

        task = session.get(HumanTask, kept(document)["review_task_id"])
        assert task is not None and task.state == "open"
        assert document.doc_type == "other", "the rules' proposal stands until someone decides"


class TestLegacyOriginals:
    def test_a_legacy_workbook_takes_its_copys_type_and_is_done(
        self, session: Session, bid: Bid, store: MemoryObjectStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def convert(payload: bytes, filename: str, source_extension: str) -> Converted:
            return Converted(
                payload=BOQ,
                filename="rates.xlsx",
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                converter="libreoffice:test",
            )

        monkeypatch.setattr("firebid.sandbox.office.convert_legacy", convert)
        legacy = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100
        legacy += "Workbook".encode("utf-16-le") + b"\x00" * 200
        outcome = Ingestor(session, store, AlwaysCleanScanner(), bid_id=bid.id).ingest(
            "rates.xls", legacy
        )
        original = next(item for item in outcome.stored if item.kind == "xls")
        copy = next(item for item in outcome.stored if item.kind == "xlsx")

        classify_in_sandbox(session, copy, BOQ)

        assert original.doc_type == "boq"
        assert original.state == "done", "read through its copy; nothing else will read it"


class TestThroughTheParseJob:
    @pytest.fixture(autouse=True)
    def the_real_store(self, store: MemoryObjectStore, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(object_store, "get_object_store", lambda: store)

    def test_a_workbook_is_classified_and_done_so_the_set_can_finish(
        self,
        session: Session,
        bid: Bid,
        user: AppUser,
        store: MemoryObjectStore,
        as_application_role: None,
    ) -> None:
        """A workbook used to stay `received` for good, so the progress never said finished."""
        document = ingest(session, bid, store, "Tender BOQ.xlsx", BOQ)
        session.commit()

        parse_document(cast(JobContext, None), document_id=str(document.id), user_id=str(user.id))

        session.expire_all()
        document = session.execute(select(Document)).scalar_one()
        assert document.state == "done"
        assert document.doc_type == "boq"
        assert read_progress(session, bid.id).finished
