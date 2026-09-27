"""Legacy `.doc` and `.xls`, converted and kept alongside the original (FR-DOC-01).

Singapore tenders still arrive with Office 97 files, so this path is not a curiosity. The
rule it protects is lineage: the original is never replaced, and the modern copy says what
made it, because a converter is a lossy step and a quantity questioned six months later has
to be traceable past it.

LibreOffice lives in the sandbox image, not in the test environment, so the converter itself
is substituted here. What is tested is what the platform does with the result — which is
where the lineage rules live.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.models.core import Bid
from firebid.db.models.documents import Document
from firebid.ingest.scanning import AlwaysCleanScanner, UnavailableScanner
from firebid.sandbox.office import ConversionFailed, Converted
from firebid.services.ingestion import Ingestor, IngestOutcome
from firebid.storage.object_store import MemoryObjectStore

pytestmark = pytest.mark.req("FR-DOC-01")


def ole(marker: bytes) -> bytes:
    """A Compound File Binary header with the stream name that identifies the application."""
    return b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100 + marker + b"\x00" * 200


LEGACY_XLS = ole(b"W\x00o\x00r\x00k\x00b\x00o\x00o\x00k\x00")
LEGACY_DOC = ole(b"W\x00o\x00r\x00d\x00D\x00o\x00c\x00u\x00m\x00e\x00n\x00t\x00")


def ooxml(marker: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr(marker, b"<xml/>")
    return buffer.getvalue()


@pytest.fixture
def converter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for LibreOffice, which lives in the sandbox image."""

    def fake(payload: bytes, filename: str, source_extension: str) -> Converted:
        if source_extension == "xls":
            return Converted(
                payload=ooxml("xl/workbook.xml"),
                filename="rates.xlsx",
                media_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                converter="libreoffice:25.2.1.2",
            )
        return Converted(
            payload=ooxml("word/document.xml"),
            filename="spec.docx",
            media_type=("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            converter="libreoffice:25.2.1.2",
        )

    monkeypatch.setattr("firebid.sandbox.office.convert_legacy", fake)


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


def ingest(
    session: Session, bid: Bid, store: MemoryObjectStore, name: str, payload: bytes
) -> IngestOutcome:
    subject = Ingestor(session, store, AlwaysCleanScanner(), bid_id=bid.id)
    return subject.ingest(name, payload)


def test_a_legacy_workbook_gains_a_modern_copy(
    session: Session, bid: Bid, store: MemoryObjectStore, converter: None
) -> None:
    outcome = ingest(session, bid, store, "rates.xls", LEGACY_XLS)

    assert [name for name, _ in outcome.converted] == ["rates.xls"]
    kinds = {document.kind for document in outcome.stored}
    assert kinds == {"xls", "xlsx"}


def test_the_original_is_kept_and_the_copy_points_back_at_it(
    session: Session, bid: Bid, store: MemoryObjectStore, converter: None
) -> None:
    """Lineage: an estimator must always be able to reach what the consultant sent."""
    ingest(session, bid, store, "spec.doc", LEGACY_DOC)

    documents = {
        document.kind: document for document in session.execute(select(Document)).scalars().all()
    }
    assert set(documents) == {"doc", "docx"}
    assert documents["docx"].derived_from_id == documents["doc"].id
    assert documents["doc"].derived_from_id is None
    assert documents["doc"].state == "received", "the original is not superseded"


def test_both_copies_are_stored(
    session: Session, bid: Bid, store: MemoryObjectStore, converter: None
) -> None:
    ingest(session, bid, store, "rates.xls", LEGACY_XLS)

    assert len(store.objects) == 2


def test_the_report_still_counts_only_what_was_uploaded(
    session: Session, bid: Bid, store: MemoryObjectStore, converter: None
) -> None:
    """One file was sent, so one file is accounted for — the copy is ours, not theirs."""
    outcome = ingest(session, bid, store, "rates.xls", LEGACY_XLS)

    assert outcome.accounted_for == 1
    assert len(outcome.stored) == 2


def test_a_failed_conversion_keeps_the_original_and_says_why(
    session: Session, bid: Bid, store: MemoryObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A password-protected .xls must not take the whole upload down with it."""

    def refuse(payload: bytes, filename: str, source_extension: str) -> Converted:
        raise ConversionFailed("this legacy Office file is password-protected")

    monkeypatch.setattr("firebid.sandbox.office.convert_legacy", refuse)

    outcome = ingest(session, bid, store, "rates.xls", LEGACY_XLS)

    assert len(outcome.stored) == 1, "the original is still registered"
    assert "password-protected" in dict(outcome.rejected)["rates.xls"]

    original = session.execute(select(Document)).scalar_one()
    assert original.kind == "xls"
    assert "could not be made" in (original.rejected_reason or "")


def test_a_sandbox_failure_during_conversion_is_reported_not_raised(
    session: Session, bid: Bid, store: MemoryObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LibreOffice hanging on one file is a reason on that file, not a failed upload."""
    from firebid.sandbox.runner import SandboxFailure

    def hang(payload: bytes, filename: str, source_extension: str) -> Converted:
        raise SandboxFailure("timeout", "converting this file took too long and was stopped")

    monkeypatch.setattr("firebid.sandbox.office.convert_legacy", hang)

    outcome = ingest(session, bid, store, "spec.doc", LEGACY_DOC)

    assert len(outcome.stored) == 1
    assert "took too long" in dict(outcome.rejected)["spec.doc"]


def test_a_modern_file_is_not_converted(
    session: Session, bid: Bid, store: MemoryObjectStore, converter: None
) -> None:
    outcome = ingest(session, bid, store, "boq.xlsx", ooxml("xl/workbook.xml"))

    assert outcome.converted == []
    assert len(outcome.stored) == 1


def test_the_converter_version_is_recorded_against_the_conversion(
    session: Session, bid: Bid, store: MemoryObjectStore, converter: None
) -> None:
    """A file that looks wrong later is answerable only if what made it is written down."""
    outcome = ingest(session, bid, store, "rates.xls", LEGACY_XLS)

    assert outcome.converted == [("rates.xls", "libreoffice:25.2.1.2")]


def test_a_legacy_workbook_held_by_an_outage_gains_its_copy_when_released(
    session: Session, bid: Bid, store: MemoryObjectStore, converter: None
) -> None:
    """A release is the rest of an upload, so it does what the upload would have done."""
    Ingestor(session, store, UnavailableScanner(), bid_id=bid.id).ingest("rates.xls", LEGACY_XLS)

    outcome = Ingestor(session, store, AlwaysCleanScanner(), bid_id=bid.id).release_held()

    assert [name for name, _ in outcome.converted] == ["rates.xls"]
    assert outcome.moved == 1, "the copy is something the platform made, not a released file"
    kinds = {document.kind for document in session.execute(select(Document)).scalars()}
    assert kinds == {"xls", "xlsx"}
