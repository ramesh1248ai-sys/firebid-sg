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

from firebid.db.audit import record_event
from firebid.db.models.core import Bid
from firebid.db.models.documents import Document, Sheet
from firebid.domain.actors import Actor, AuditContext
from firebid.ingest.archives import ArchiveRefused, expand
from firebid.ingest.detection import (
    LEGACY_OFFICE,
    PARSEABLE,
    Detected,
    FileKind,
    detect,
    dwg_supported,
)
from firebid.ingest.origin import STORED, Origin, clean_path, propose
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
REASON_RAR = "RAR archives cannot be opened; upload the folder itself, or re-pack it as a ZIP"
RAR_MAGIC = b"Rar!\x1a\x07"
NOT_READ = "kept, not read: only tender documents are read"


@dataclass
class IngestOutcome:
    """What happened to one upload, whole."""

    stored: list[Document] = field(default_factory=list)
    duplicates: list[Document] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    quarantined: list[tuple[str, str]] = field(default_factory=list)
    held: list[tuple[str, str]] = field(default_factory=list)
    # (original filename, converter) for each legacy file given a modern copy.
    converted: list[tuple[str, str]] = field(default_factory=list)
    # Files a rescan released from `awaiting_scan`, clean or not. Only a release sets it.
    moved: int = 0
    # Files that are not documents (an office lock file, Thumbs.db): reported, never stored.
    ignored: list[tuple[str, str]] = field(default_factory=list)

    @property
    def accounted_for(self) -> int:
        """How many of the files the estimator sent are accounted for.

        A converted copy is not one of them: it is something the platform made, so counting
        it would make the report stop matching what was uploaded.
        """
        return (
            len(self.stored)
            - len(self.converted)
            + len(self.duplicates)
            + len(self.rejected)
            + len(self.quarantined)
            + len(self.held)
            + len(self.ignored)
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
        created_by: str | None = None,
    ) -> None:
        self._session = session
        self._store = store
        self._scanner = scanner
        self._bid_id = bid_id
        self._package_id = tender_package_id
        self._created_by_id = created_by_id
        self._created_by = created_by

    def ingest(
        self,
        filename: str,
        payload: bytes,
        *,
        source_path: str | None = None,
        origin: Origin | None = None,
    ) -> IngestOutcome:
        """Take one uploaded file. An archive is expanded; everything else is one document.

        `source_path` is the file's path in the folder it came from. `origin` is what the
        person sending it said it is; without one it is proposed from the path, and anything
        proposed as other than a tender document waits for a person (FR-DOC-10).
        """
        outcome = IngestOutcome()
        path = clean_path(source_path) if source_path else None

        if not payload:
            outcome.rejected.append((filename, REASON_EMPTY))
            return outcome
        if origin is Origin.IGNORED:
            outcome.ignored.append((filename, "left out by the person who sent the folder"))
            return outcome

        kind = detect(payload, filename).kind
        if kind is FileKind.ZIP:
            self._ingest_archive(filename, payload, outcome, path, origin)
        else:
            self._ingest_one(filename, payload, outcome, path, origin)
        return outcome

    def _ingest_archive(
        self,
        filename: str,
        payload: bytes,
        outcome: IngestOutcome,
        path: str | None,
        origin: Origin | None,
    ) -> None:
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

        inside = path or filename
        for entry in entries:
            # A file inside an archive nobody has looked into: what the sender said holds
            # only if they said it is not tender input. Otherwise each entry's own path
            # decides, so a marked-up copy inside a tender zip still waits for a person.
            said = origin if origin not in (None, Origin.TENDER) else None
            self._ingest_one(entry.name, entry.payload, outcome, f"{inside}/{entry.name}", said)

    def _ingest_one(
        self,
        filename: str,
        payload: bytes,
        outcome: IngestOutcome,
        path: str | None = None,
        origin: Origin | None = None,
    ) -> None:
        proposal = propose(path or filename)
        if proposal.origin is Origin.IGNORED:
            outcome.ignored.append((filename, proposal.reason))
            return
        if payload[: len(RAR_MAGIC)] == RAR_MAGIC:
            outcome.rejected.append((filename, REASON_RAR))
            return
        self._path, self._origin, self._proposal = path, origin, proposal
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
        self._accept(document, payload, detected, outcome)

    def _accept(
        self, document: Document, payload: bytes, detected: Detected, outcome: IngestOutcome
    ) -> None:
        """A file that scanned clean: ready to be read, and given a modern copy if it needs one."""
        document.state = "received" if readable(document) else "done"
        document.rejected_reason = None
        document.scanned_at = datetime.now(UTC)
        outcome.stored.append(document)

        if detected.kind in LEGACY_OFFICE:
            self._convert_legacy(document, payload, detected, outcome)

    def release_held(self) -> IngestOutcome:
        """Scan again the files an outage held, and carry on with them as an upload would.

        Without this an outage would leave a tender set stuck for good: the point of holding
        is that the scan happens later, not never. A released file takes the rest of the
        upload path, so it is converted if it needs to be and handed on to be read; the caller
        queues the reading, exactly as it does after an upload.
        """
        outcome = IngestOutcome()
        held = (
            self._session.execute(
                select(Document).where(
                    Document.bid_id == self._bid_id, Document.state == "awaiting_scan"
                )
            )
            .scalars()
            .all()
        )

        for document in held:
            payload = self._store.get(document.storage_key)
            result = self._scanner.scan(payload)
            if result.verdict is Verdict.UNAVAILABLE:
                outcome.held.append((document.filename, result.detail))
                continue
            outcome.moved += 1
            if result.verdict is Verdict.INFECTED:
                document.state = "quarantined"
                document.rejected_reason = f"malware found: {result.signature}"
                document.scanned_at = datetime.now(UTC)
                document.scan_signature = result.signature[:200]
                outcome.quarantined.append((document.filename, result.signature))
                log.warning(
                    "file_quarantined", filename=document.filename, signature=result.signature
                )
                continue
            self._accept(document, payload, detect(payload, document.filename), outcome)

        self._session.flush()
        if outcome.moved:
            log.info("rescan_completed", bid_id=str(self._bid_id), moved=outcome.moved)
        return outcome

    _path: str | None = None
    _origin: Origin | None = None
    _proposal = propose("")

    def _origin_fields(self) -> dict[str, object]:
        """Whose document the file being registered is, and who or what said so."""
        if self._origin is not None:
            return {
                "origin": str(self._origin),
                "origin_status": "confirmed",
                "origin_reason": "said by the person who sent it",
                "origin_by": self._created_by,
                "origin_at": datetime.now(UTC),
            }
        return {
            "origin": str(self._proposal.origin),
            # A tender document is what an upload is unless something says otherwise; any
            # other proposal waits for a person.
            "origin_status": "confirmed" if self._proposal.origin is Origin.TENDER else "proposed",
            "origin_reason": self._proposal.reason,
        }

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

    def _convert_legacy(
        self,
        original: Document,
        payload: bytes,
        detected: Detected,
        outcome: IngestOutcome,
    ) -> None:
        """Convert a `.doc` or `.xls` and register the result as a derived document.

        The original is kept and the conversion points back at it, because a converter is a
        lossy step and the lineage has to lead past it to what the consultant actually sent.
        A conversion that fails does not fail the upload: the original is registered, and the
        failure is visible as a reason on the derived document that was not created.
        """
        from firebid.sandbox.office import ConversionFailed, convert_legacy
        from firebid.sandbox.runner import SandboxFailure

        try:
            converted = convert_legacy(payload, original.filename, str(detected.kind))
        except (ConversionFailed, SandboxFailure) as failure:
            reason = getattr(failure, "reason", str(failure))
            original.rejected_reason = f"the modern copy could not be made: {reason}"
            outcome.rejected.append((original.filename, original.rejected_reason))
            log.warning("legacy_conversion_failed", filename=original.filename, reason=reason)
            return

        digest = checksum(converted.payload)
        if existing_document(self._session, self._bid_id, digest) is not None:
            return

        derived = self._register(
            converted.filename,
            converted.payload,
            digest,
            Detected(
                FileKind.XLSX if detected.kind is FileKind.XLS else FileKind.DOCX,
                converted.media_type,
            ),
            state="received",
        )
        derived.derived_from_id = original.id
        derived.origin, derived.origin_status = original.origin, original.origin_status
        derived.origin_reason, derived.origin_by = original.origin_reason, original.origin_by
        derived.state = "received" if readable(derived) else "done"
        derived.scanned_at = original.scanned_at
        # What produced it, so a file that looks wrong later is answerable.
        derived.rejected_reason = None
        outcome.stored.append(derived)
        outcome.converted.append((original.filename, converted.converter))
        log.info(
            "legacy_document_converted",
            original=str(original.id),
            derived=str(derived.id),
            converter=converted.converter,
        )

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
            source_path=(self._path or "")[:1024] or None,
            **self._origin_fields(),
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


def readable(document: Document) -> bool:
    """Whether a document may be read: a tender document whose origin is confirmed."""
    return document.origin == str(Origin.TENDER) and document.origin_status == "confirmed"


def set_origin(
    session: Session, document: Document, origin: Origin, actor: Actor, reason: str | None = None
) -> bool:
    """A person says whose document this is. Returns whether it now needs to be read.

    A document already read as tender input cannot be taken out of the registers here: its
    sheets and revisions are in use. It is refused, with what to do instead.
    """
    if str(origin) not in STORED:
        raise ValueError("a stored document is a tender, working or reference document")
    was_read = (
        session.execute(select(Sheet.id).where(Sheet.document_id == document.id).limit(1)).first()
        is not None
    )
    if origin is not Origin.TENDER and readable(document) and was_read:
        raise ValueError(
            "this document has already been read as tender input; its sheets are in the "
            "registers. Mark its revisions superseded in the drawing register instead"
        )
    before = {"origin": document.origin, "origin_status": document.origin_status}
    needs_reading = origin is Origin.TENDER and not (readable(document) and was_read)
    document.origin = str(origin)
    document.origin_status = "confirmed"
    document.origin_reason = reason or f"set by {actor.label}"
    document.origin_by = actor.label
    document.origin_at = datetime.now(UTC)
    if document.state in ("received", "done"):
        document.state = "received" if needs_reading else "done"
    session.flush()
    organisation_id = session.execute(
        select(Bid.organisation_id).where(Bid.id == document.bid_id)
    ).scalar_one()
    record_event(
        session,
        context=AuditContext(organisation_id=organisation_id, bid_id=document.bid_id),
        actor=actor,
        action="document origin: set",
        entity_type=Document.__tablename__,
        entity_id=document.id,
        before=before,
        after={"origin": document.origin, "origin_status": "confirmed"},
        reason=reason,
    )
    return needs_reading and document.state == "received"


def legacy_needs_conversion(document: Document) -> bool:
    return document.kind in {str(kind) for kind in LEGACY_OFFICE}
