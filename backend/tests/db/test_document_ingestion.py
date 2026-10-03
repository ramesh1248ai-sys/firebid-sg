"""Storing, scanning and registering an upload (FR-DOC-01, NFR-06).

The three behaviours that matter most are here, and each one is a rule that is tempting to
break under pressure: infected files never reach a parser, a scanner outage holds rather than
passes, and the same bytes are stored once.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.models.core import Bid
from firebid.db.models.documents import Document
from firebid.ingest.scanning import (
    AlwaysCleanScanner,
    Scanner,
    ScanResult,
    UnavailableScanner,
    Verdict,
)
from firebid.services.ingestion import (
    REASON_DWG_UNAVAILABLE,
    Ingestor,
    checksum,
    storage_key,
)
from firebid.storage.object_store import MemoryObjectStore

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<</Type/Catalog>>\nendobj\n"
DXF = b"  0\r\nSECTION\r\n  2\r\nHEADER\r\n  0\r\nENDSEC\r\n  0\r\nEOF\r\n"
DWG = b"AC1027" + b"\x00" * 64

# The EICAR anti-malware test file, split so this source file is not itself flagged by a
# desktop scanner. It is a harmless string every scanner recognises, by agreement.
EICAR = (r"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE" + "!$H+H*").encode()


class EicarScanner(Scanner):
    """Recognises the EICAR string, the way a real scanner does, and nothing else."""

    def __init__(self) -> None:
        self.scanned: list[int] = []

    def scan(self, payload: bytes) -> ScanResult:
        self.scanned.append(len(payload))
        if b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in payload:
            return ScanResult(Verdict.INFECTED, signature="Eicar-Test-Signature")
        return ScanResult(Verdict.CLEAN)


class RecordingParser:
    """Stands in for the parsing stage, purely to prove it is never reached."""

    def __init__(self) -> None:
        self.opened: list[str] = []

    def parse(self, document: Document) -> None:
        self.opened.append(document.filename)


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


def zip_of(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def ingestor(session: Session, bid: Bid, store: MemoryObjectStore, scanner: Scanner) -> Ingestor:
    return Ingestor(session, store, scanner, bid_id=bid.id)


@pytest.mark.req("NFR-06")
def test_an_infected_file_is_quarantined_and_never_reaches_a_parser(
    session: Session, bid: Bid, store: MemoryObjectStore
) -> None:
    scanner = EicarScanner()
    parser = RecordingParser()

    outcome = ingestor(session, bid, store, scanner).ingest("virus.pdf", PDF + EICAR)

    assert outcome.quarantined == [("virus.pdf", "Eicar-Test-Signature")]
    assert outcome.stored == []

    document = session.execute(select(Document)).scalar_one()
    assert document.state == "quarantined"
    assert document.scan_signature == "Eicar-Test-Signature"
    assert "Eicar" in (document.rejected_reason or "")

    # The parsing stage only ever sees files in `received`, which this is not.
    for ready in session.execute(select(Document).where(Document.state == "received")).scalars():
        parser.parse(ready)
    assert parser.opened == [], "a quarantined file must never be opened"


@pytest.mark.req("NFR-06")
def test_an_infected_file_inside_an_archive_is_caught_and_its_neighbours_are_not(
    session: Session, bid: Bid, store: MemoryObjectStore
) -> None:
    """One bad file in a 300-sheet set refuses one file, not the set."""
    payload = zip_of({"A-01.pdf": PDF, "nasty.pdf": PDF + EICAR, "A-02.pdf": PDF + b"two"})

    outcome = ingestor(session, bid, store, EicarScanner()).ingest("set.zip", payload)

    assert [name for name, _ in outcome.quarantined] == ["nasty.pdf"]
    assert sorted(document.filename for document in outcome.stored) == ["A-01.pdf", "A-02.pdf"]
    assert outcome.accounted_for == 3


@pytest.mark.req("FR-DOC-09")
def test_an_archive_is_taken_a_file_at_a_time_not_expanded_whole(
    session: Session, bid: Bid, store: MemoryObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tender set expands to many times its archive: only one of its files is held."""
    import gc
    import weakref

    from firebid.ingest import archives
    from firebid.services import ingestion

    held: list[int] = []
    seen: list[weakref.ref[archives.ArchiveEntry]] = []

    def watched(payload: bytes, depth: int = 0) -> Iterator[archives.ArchiveEntry]:
        for entry in archives.expand(payload, depth):
            gc.collect()
            held.append(sum(1 for earlier in seen if earlier() is not None))
            seen.append(weakref.ref(entry))
            yield entry

    monkeypatch.setattr(ingestion, "expand", watched)
    files = {f"A-{n:02d}.pdf": PDF + str(n).encode() for n in range(12)}

    outcome = ingestor(session, bid, store, AlwaysCleanScanner()).ingest("set.zip", zip_of(files))

    assert len(outcome.stored) == 12
    assert max(held) <= 1, "earlier entries were still held when a later one was read"


@pytest.mark.req("FR-DOC-01")
def test_an_archive_refused_part_of_the_way_in_stores_none_of_it(
    session: Session, bid: Bid, store: MemoryObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A header that lies is only found on reading: by then nothing may have been kept."""
    from firebid.ingest import archives
    from firebid.services import ingestion

    def lying(payload: bytes, depth: int = 0) -> Iterator[archives.ArchiveEntry]:
        yield archives.ArchiveEntry("A-01.pdf", PDF, 0)
        raise archives.ArchiveRefused("'A-02.pdf' is larger than its header claims")

    monkeypatch.setattr(ingestion, "expand", lying)

    archive = zip_of({"A-01.pdf": PDF, "A-02.pdf": PDF + b"two"})

    outcome = ingestor(session, bid, store, AlwaysCleanScanner()).ingest("set.zip", archive)

    assert outcome.stored == [] and store.objects == {}
    assert outcome.rejected == [("set.zip", "'A-02.pdf' is larger than its header claims")]
    assert session.execute(select(Document)).first() is None


@pytest.mark.req("NFR-06")
def test_a_scanner_outage_holds_files_rather_than_passing_them(
    session: Session, bid: Bid, store: MemoryObjectStore
) -> None:
    outcome = ingestor(session, bid, store, UnavailableScanner()).ingest("A-01.pdf", PDF)

    assert outcome.stored == []
    assert [name for name, _ in outcome.held] == ["A-01.pdf"]

    document = session.execute(select(Document)).scalar_one()
    assert document.state == "awaiting_scan"
    assert document.scanned_at is None, "nothing was scanned, so there is no scan time"


@pytest.mark.req("NFR-06")
def test_a_held_file_moves_on_when_the_scanner_returns(
    session: Session, bid: Bid, store: MemoryObjectStore
) -> None:
    ingestor(session, bid, store, UnavailableScanner()).ingest("A-01.pdf", PDF)

    outcome = ingestor(session, bid, store, AlwaysCleanScanner()).release_held()

    document = session.execute(select(Document)).scalar_one()
    assert outcome.stored == [document], "released like a fresh upload, so it is parsed next"
    assert outcome.moved == 1
    assert document.state == "received"
    assert document.rejected_reason is None
    assert document.scanned_at is not None


@pytest.mark.req("NFR-06")
def test_a_rescan_that_finds_malware_quarantines_the_held_file(
    session: Session, bid: Bid, store: MemoryObjectStore
) -> None:
    ingestor(session, bid, store, UnavailableScanner()).ingest("late.pdf", PDF + EICAR)

    outcome = ingestor(session, bid, store, EicarScanner()).release_held()

    assert outcome.quarantined == [("late.pdf", "Eicar-Test-Signature")]
    assert outcome.stored == [], "a quarantined file is never handed on to be parsed"
    assert outcome.moved == 1

    document = session.execute(select(Document)).scalar_one()
    assert document.state == "quarantined"
    assert document.scan_signature == "Eicar-Test-Signature"


@pytest.mark.req("NFR-06")
def test_a_rescan_during_a_continuing_outage_leaves_the_file_held(
    session: Session, bid: Bid, store: MemoryObjectStore
) -> None:
    ingestor(session, bid, store, UnavailableScanner()).ingest("A-01.pdf", PDF)

    outcome = ingestor(session, bid, store, UnavailableScanner()).release_held()

    assert outcome.moved == 0
    assert outcome.stored == []
    assert session.execute(select(Document)).scalar_one().state == "awaiting_scan"


@pytest.mark.req("FR-DOC-01")
def test_re_uploading_an_identical_file_creates_no_second_stored_object(
    session: Session, bid: Bid, store: MemoryObjectStore
) -> None:
    subject = ingestor(session, bid, store, AlwaysCleanScanner())

    first = subject.ingest("A-01.pdf", PDF)
    second = subject.ingest("A-01 (copy).pdf", PDF)

    assert len(first.stored) == 1
    assert second.stored == []
    assert [document.id for document in second.duplicates] == [first.stored[0].id]

    assert len(store.objects) == 1
    assert store.writes == 1, "the same bytes must not be written twice"
    assert session.execute(select(Document)).scalars().all() == [first.stored[0]]


@pytest.mark.req("FR-DOC-01")
def test_the_same_bytes_on_two_bids_are_stored_separately(
    session: Session, bid: Bid, second_bid: Bid, store: MemoryObjectStore
) -> None:
    """Deduplication is per bid: two clients' sets stay separate, whatever they contain."""
    ingestor(session, bid, store, AlwaysCleanScanner()).ingest("A-01.pdf", PDF)
    ingestor(session, second_bid, store, AlwaysCleanScanner()).ingest("A-01.pdf", PDF)

    assert len(store.objects) == 2
    assert len(session.execute(select(Document)).scalars().all()) == 2


@pytest.mark.req("FR-DOC-01")
def test_every_file_in_a_mixed_upload_is_accounted_for(
    session: Session, bid: Bid, store: MemoryObjectStore
) -> None:
    """The report must balance: nothing may go missing quietly."""
    payload = zip_of(
        {
            "A-01.pdf": PDF,
            "GA.dxf": DXF,
            "old.dwg": DWG,
            "notes.exe": b"MZ\x90\x00" + b"\x00" * 64,
            "A-01-again.pdf": PDF,
        }
    )

    outcome = ingestor(session, bid, store, AlwaysCleanScanner()).ingest("set.zip", payload)

    assert outcome.accounted_for == 5
    assert sorted(document.filename for document in outcome.stored) == ["A-01.pdf", "GA.dxf"]
    assert [document.filename for document in outcome.duplicates] == ["A-01.pdf"]
    reasons = dict(outcome.rejected)
    assert reasons["old.dwg"] == REASON_DWG_UNAVAILABLE
    assert "not one the platform can read" in reasons["notes.exe"]


@pytest.mark.req("FR-DOC-01")
def test_a_dwg_is_refused_with_a_reason_that_names_the_way_forward(
    session: Session, bid: Bid, store: MemoryObjectStore
) -> None:
    outcome = ingestor(session, bid, store, AlwaysCleanScanner()).ingest("PLAN.dwg", DWG)

    reason = dict(outcome.rejected)["PLAN.dwg"]
    assert "DXF" in reason, "the estimator needs to know what to send instead"
    document = session.execute(select(Document)).scalar_one()
    assert document.state == "rejected"


@pytest.mark.req("FR-DOC-01")
def test_an_unreadable_file_is_recorded_rather_than_dropped(
    session: Session, bid: Bid, store: MemoryObjectStore
) -> None:
    """A refusal is a row. Otherwise the file is simply gone, and nobody knows to chase it."""
    outcome = ingestor(session, bid, store, AlwaysCleanScanner()).ingest("thing.bin", b"\x00" * 99)

    assert len(outcome.rejected) == 1
    document = session.execute(select(Document)).scalar_one()
    assert document.state == "rejected"
    assert document.filename == "thing.bin"


@pytest.mark.req("FR-DOC-01")
def test_an_empty_file_is_refused_without_being_stored(
    session: Session, bid: Bid, store: MemoryObjectStore
) -> None:
    outcome = ingestor(session, bid, store, AlwaysCleanScanner()).ingest("empty.pdf", b"")

    assert outcome.rejected == [("empty.pdf", "the file is empty")]
    assert store.objects == {}


@pytest.mark.req("FR-DOC-01")
def test_a_refused_file_is_never_scanned(
    session: Session, bid: Bid, store: MemoryObjectStore
) -> None:
    """A scan costs time; a file that cannot be read is refused before spending it."""
    scanner = EicarScanner()

    ingestor(session, bid, store, scanner).ingest("thing.bin", b"\x00" * 99)

    assert scanner.scanned == []


@pytest.mark.req("FR-DOC-01")
def test_the_storage_key_is_the_content_digest(bid: Bid) -> None:
    key = storage_key(bid.id, checksum(PDF), "A-01.pdf")

    assert checksum(PDF) in key
    assert key.startswith(f"bids/{bid.id}/")
    assert key.endswith(".pdf")
