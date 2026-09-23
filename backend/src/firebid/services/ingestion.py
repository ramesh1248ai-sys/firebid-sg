"""Taking a file from "uploaded" to "ready to parse", or to a visible refusal.

The rule that shapes this module: **no file disappears without a state**. Every upload ends in
one of `done`, `rejected`, `quarantined` or `awaiting_scan`, and each carries a reason a person
can read. A tender set where four files silently vanished is worse than one that refused them
loudly, because the estimator prices what they can see.

The order is not negotiable (guardrail 9): store, scan, and only then parse. A file is never
opened before it is cleared.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.models.documents import Document
from firebid.ingest.archives import ArchiveRefused, expand
from firebid.ingest.detection import (
    LEGACY_OFFICE,
    PARSEABLE,
    Detected,
    FileKind,
    detect,
    dwg_supported,
)
from firebid.ingest.scanning import Scanner, Verdict
from firebid.storage.object_store import ObjectExists, ObjectStore

log = structlog.get_logger("firebid.ingest")

# Reasons, written once so the API, the UI and the tests agree on the wording.
REASON_UNSUPPORTED = "this file type is not one the platform can read"
REASON_DWG_UNAVAILABLE = (
    "DWG conversion is not available yet; the converter licence is pending (ADR-003). "
    "Send the DXF export of this drawing and it will be read."
)
REASON_EMPTY = "the file is empty"


@dataclass
class IngestOutcome:
    """What happened to one upload, whole."""

    stored: list[Document] = field(default_factory=list)
    duplicates: list[Document] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    quarantined: list[tuple[str, str]] = field(default_factory=list)
    held: list[tuple[str, str]] = field(default_factory=list)

    @property
    def accounted_for(self) -> int:
        return (
            len(self.stored)
            + len(self.duplicates)
            + len(self.rejected)
            + len(self.quarantined)
            + len(self.held)
        )


def checksum(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def storage_key(bid_id: uuid.UUID, digest: str, filename: str) -> str:
    """Content-addressed, so the same bytes are stored once however often they arrive."""
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"bids/{bid_id}/documents/{digest}.{suffix[:12]}"


def existing_document(session: Session, bid_id: uuid.UUID, digest: str) -> Document | None:
    return session.execute(
        select(Document).where(Document.bid_id == bid_id, Document.sha256 == digest)
    ).scalar_one_or_none()


class Ingestor:
    """Stores, scans and registers uploads for one bid."""

    def __init__(
        self,
        session: Session,
        store: ObjectStore,
        scanner: Scanner,
        *,
        bid_id: uuid.UUID,
        tender_package_id: uuid.UUID | None = None,
        created_by_id: uuid.UUID | None = None,
    ) -> None:
        self._session = session
        self._store = store
        self._scanner = scanner
        self._bid_id = bid_id
        self._package_id = tender_package_id
        self._created_by_id = created_by_id

    def ingest(self, filename: str, payload: bytes) -> IngestOutcome:
        """Take one uploaded file. An archive is expanded; everything else is one document."""
        outcome = IngestOutcome()

        if not payload:
            outcome.rejected.append((filename, REASON_EMPTY))
            return outcome

        kind = detect(payload, filename).kind
        if kind is FileKind.ZIP:
            self._ingest_archive(filename, payload, outcome)
        else:
            self._ingest_one(filename, payload, outcome)
        return outcome

    def _ingest_archive(self, filename: str, payload: bytes, outcome: IngestOutcome) -> None:
        """A whole set in one upload. A refused archive refuses as one thing, not silently."""
        try:
            entries = list(expand(payload))
        except ArchiveRefused as refusal:
            outcome.rejected.append((filename, str(refusal)))
            log.warning("archive_refused", filename=filename, reason=str(refusal))
            return

        if not entries:
            outcome.rejected.append((filename, "the archive holds no files"))
            return

        for entry in entries:
            self._ingest_one(entry.name, entry.payload, outcome)

    def _ingest_one(self, filename: str, payload: bytes, outcome: IngestOutcome) -> None:
        digest = checksum(payload)

        already = existing_document(self._session, self._bid_id, digest)
        if already is not None:
            # The same bytes on this bid: one stored object, one document, whatever the name.
            outcome.duplicates.append(already)
            log.info("duplicate_upload", filename=filename, document_id=str(already.id))
            return

        detected = detect(payload, filename)
        refusal = self._refusal_for(detected)
        if refusal is not None:
            document = self._register(filename, payload, digest, detected, state="rejected")
            document.rejected_reason = refusal
            outcome.rejected.append((filename, refusal))
            return

        result = self._scanner.scan(payload)
        if result.verdict is Verdict.INFECTED:
            # Stored, so it can be handed to whoever needs to know, but never opened.
            document = self._register(filename, payload, digest, detected, state="quarantined")
            document.rejected_reason = f"malware found: {result.signature}"
            document.scanned_at = datetime.now(UTC)
            document.scan_signature = result.signature[:200]
            outcome.quarantined.append((filename, result.signature))
            log.warning("file_quarantined", filename=filename, signature=result.signature)
            return

        if result.verdict is Verdict.UNAVAILABLE:
            # Held, not passed. `scanned_at` stays null: no scan happened.
            document = self._register(filename, payload, digest, detected, state="awaiting_scan")
            document.rejected_reason = result.detail
            outcome.held.append((filename, result.detail))
            return

        document = self._register(filename, payload, digest, detected, state="received")
        document.scanned_at = datetime.now(UTC)
        outcome.stored.append(document)

    def _refusal_for(self, detected: Detected) -> str | None:
        """Whether this kind can be read at all, before spending a scan on it."""
        if detected.kind is FileKind.DWG and not dwg_supported():
            return REASON_DWG_UNAVAILABLE
        if detected.kind is FileKind.UNKNOWN:
            return (
                f"{REASON_UNSUPPORTED} ({detected.detail})"
                if detected.detail
                else REASON_UNSUPPORTED
            )
        if detected.kind not in PARSEABLE and detected.kind is not FileKind.DWG:
            if detected.kind is FileKind.IMAGE:
                return None  # images are registered; P1-03 decides what to do with them
            return REASON_UNSUPPORTED
        return None

    def _register(
        self,
        filename: str,
        payload: bytes,
        digest: str,
        detected: Detected,
        *,
        state: str,
    ) -> Document:
        key = storage_key(self._bid_id, digest, filename)
        try:
            self._store.put_once(key, payload, content_type=detected.media_type)
        except ObjectExists:
            # The key is the content hash, so the object already there *is* these bytes. This
            # happens when a document row was removed but the write-once object survived it.
            log.info("object_already_stored", key=key)

        document = Document(
            bid_id=self._bid_id,
            tender_package_id=self._package_id,
            filename=filename[:512],
            media_type=detected.media_type,
            kind=str(detected.kind),
            sha256=digest,
            byte_size=len(payload),
            storage_key=key,
            state=state,
            created_by_id=self._created_by_id,
        )
        self._session.add(document)
        self._session.flush()
        log.info(
            "document_registered",
            document_id=str(document.id),
            kind=str(detected.kind),
            state=state,
            byte_size=len(payload),
        )
        return document


def rescan_held(session: Session, store: ObjectStore, scanner: Scanner, bid_id: uuid.UUID) -> int:
    """Retry files held by an outage. Returns how many moved on.

    Without this, an outage would leave a tender set stuck for good: the point of holding is
    that the scan happens later, not never.
    """
    held = (
        session.execute(
            select(Document).where(Document.bid_id == bid_id, Document.state == "awaiting_scan")
        )
        .scalars()
        .all()
    )

    moved = 0
    for document in held:
        result = scanner.scan(store.get(document.storage_key))
        if result.verdict is Verdict.UNAVAILABLE:
            continue
        document.scanned_at = datetime.now(UTC)
        if result.verdict is Verdict.INFECTED:
            document.state = "quarantined"
            document.rejected_reason = f"malware found: {result.signature}"
            document.scan_signature = result.signature[:200]
        else:
            document.state = "received"
            document.rejected_reason = None
        moved += 1

    session.flush()
    if moved:
        log.info("rescan_completed", bid_id=str(bid_id), moved=moved)
    return moved


def legacy_needs_conversion(document: Document) -> bool:
    return document.kind in {str(kind) for kind in LEGACY_OFFICE}
