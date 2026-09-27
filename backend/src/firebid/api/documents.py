"""Uploading a tender set, and seeing what became of every file (FR-DOC-01).

Two ways in, because a 300-sheet set and a single addendum are different problems:

* `POST /bids/{bid_id}/documents` takes files directly. Simple, and right up to a few hundred
  megabytes.
* `POST /bids/{bid_id}/documents/uploads` hands back a presigned URL per file so the browser
  puts the bytes straight into object storage, then `.../uploads/{key}/complete` registers
  what landed. A dropped connection costs one file, not the whole set.

The response always accounts for every file: what was stored, what was a duplicate, what was
refused and why. A silent omission is the failure this endpoint exists to prevent.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.api.deps import BidContext, CurrentBid, DbSession, require
from firebid.auth.permissions import Action
from firebid.auth.provisioning import Principal
from firebid.db.models.documents import Document
from firebid.ingest.scanning import get_scanner
from firebid.services.ingestion import (
    Ingestor,
    IngestOutcome,
    checksum,
    existing_document,
    rescan_held,
    storage_key,
)
from firebid.storage.object_store import get_object_store

router = APIRouter(prefix="/bids/{bid_id}/documents", tags=["documents"])

# Bigger than any single tender drawing we have seen; an archive of a whole set goes through
# the presigned route instead.
MAX_DIRECT_UPLOAD_BYTES = 200 * 1024 * 1024
PRESIGN_TTL_SECONDS = 3600


class DocumentOut(BaseModel):
    id: uuid.UUID
    filename: str
    media_type: str
    kind: str | None
    sha256: str
    byte_size: int
    state: str
    rejected_reason: str | None = None

    model_config = {"from_attributes": True}


class RefusedOut(BaseModel):
    filename: str
    reason: str


class UploadReport(BaseModel):
    """Every file, accounted for. `accounted_for` should equal what the client sent."""

    stored: list[DocumentOut] = []
    duplicates: list[DocumentOut] = []
    rejected: list[RefusedOut] = []
    quarantined: list[RefusedOut] = []
    awaiting_scan: list[RefusedOut] = []
    accounted_for: int = 0


class PresignRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=512)
    byte_size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PresignOut(BaseModel):
    filename: str
    storage_key: str
    upload_url: str | None
    expires_in: int = PRESIGN_TTL_SECONDS
    already_uploaded: bool = False


def _report(outcome: IngestOutcome) -> UploadReport:
    return UploadReport(
        stored=[DocumentOut.model_validate(d) for d in outcome.stored],
        duplicates=[DocumentOut.model_validate(d) for d in outcome.duplicates],
        rejected=[RefusedOut(filename=n, reason=r) for n, r in outcome.rejected],
        quarantined=[
            RefusedOut(filename=n, reason=f"malware found: {s}") for n, s in outcome.quarantined
        ],
        awaiting_scan=[RefusedOut(filename=n, reason=r) for n, r in outcome.held],
        accounted_for=outcome.accounted_for,
    )


PARSEABLE_KINDS = frozenset({"pdf", "dxf"})


def _queue_parsing(session: Session, report: UploadReport, user_id: uuid.UUID) -> None:
    """Queue a parse job for each newly stored document that becomes sheets.

    In the caller's transaction (the `jobs` convention): a rollback takes the jobs with it,
    so a job never runs against a document row that was never committed. The job acts as
    the uploader, since row-level security shows it nothing otherwise.
    """
    from firebid.jobs.enqueue import enqueue
    from firebid.jobs.tasks import parse_document

    for document in report.stored:
        if document.kind in PARSEABLE_KINDS:
            enqueue(session, parse_document, document_id=str(document.id), user_id=str(user_id))


def _merge(into: UploadReport, addition: UploadReport) -> None:
    into.stored += addition.stored
    into.duplicates += addition.duplicates
    into.rejected += addition.rejected
    into.quarantined += addition.quarantined
    into.awaiting_scan += addition.awaiting_scan
    into.accounted_for += addition.accounted_for


def _ingestor(session: Session, context: BidContext) -> Ingestor:
    return Ingestor(
        session,
        get_object_store(),
        get_scanner(),
        bid_id=context.bid.id,
        created_by_id=context.principal.user_id,
    )


@router.post("", response_model=UploadReport, status_code=status.HTTP_201_CREATED)
def upload(
    context: CurrentBid,
    session: DbSession,
    _: Annotated[Principal, require(Action.DOCUMENT_UPLOAD)],
    files: Annotated[list[UploadFile], File()],
) -> UploadReport:
    """Upload one or more files, or one archive holding a whole set."""
    ingestor = _ingestor(session, context)
    report = UploadReport()

    for upload_file in files:
        payload = upload_file.file.read()
        if len(payload) > MAX_DIRECT_UPLOAD_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"'{upload_file.filename}' is larger than "
                f"{MAX_DIRECT_UPLOAD_BYTES // (1024 * 1024)} MB; use the presigned upload route",
            )
        _merge(report, _report(ingestor.ingest(upload_file.filename or "unnamed", payload)))

    _queue_parsing(session, report, context.principal.user_id)
    return report


@router.post("/uploads", response_model=list[PresignOut])
def presign(
    context: CurrentBid,
    session: DbSession,
    _: Annotated[Principal, require(Action.DOCUMENT_UPLOAD)],
    body: list[PresignRequest],
) -> list[PresignOut]:
    """Ask for somewhere to put each file. A digest already on this bid needs no upload."""
    store = get_object_store()
    answers: list[PresignOut] = []

    for request in body:
        key = storage_key(context.bid.id, request.sha256, request.filename)
        if existing_document(session, context.bid.id, request.sha256) is not None:
            # The bytes are already here: skip the transfer entirely. This is what makes
            # re-sending a 2 GB set after an addendum cheap.
            answers.append(
                PresignOut(
                    filename=request.filename,
                    storage_key=key,
                    upload_url=None,
                    already_uploaded=True,
                )
            )
            continue
        answers.append(
            PresignOut(
                filename=request.filename,
                storage_key=key,
                upload_url=store.presigned_url(key, expires_in=PRESIGN_TTL_SECONDS),
            )
        )

    return answers


class CompleteRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=512)
    storage_key: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.post("/uploads/complete", response_model=UploadReport)
def complete(
    context: CurrentBid,
    session: DbSession,
    _: Annotated[Principal, require(Action.DOCUMENT_UPLOAD)],
    body: list[CompleteRequest],
) -> UploadReport:
    """Register bytes that went straight to object storage.

    The digest is recomputed here rather than trusted: the browser computed the one it sent,
    and this is the point where the file stops being the client's word and becomes ours.
    """
    store = get_object_store()
    ingestor = _ingestor(session, context)
    report = UploadReport()

    for request in body:
        expected = storage_key(context.bid.id, request.sha256, request.filename)
        if request.storage_key != expected:
            # The key is derived, not chosen; a key from elsewhere would read another bid's data.
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"'{request.filename}' was not uploaded to the key it was given",
            )
        try:
            payload = store.get(request.storage_key)
        except Exception as error:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"'{request.filename}' is not in storage; upload it before completing",
            ) from error

        if checksum(payload) != request.sha256:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"'{request.filename}' does not match the digest declared for it; "
                "the upload was corrupted or truncated",
            )
        _merge(report, _report(ingestor.ingest(request.filename, payload)))

    _queue_parsing(session, report, context.principal.user_id)
    return report


@router.get("", response_model=list[DocumentOut])
def list_documents(context: CurrentBid, session: DbSession) -> list[DocumentOut]:
    """Everything on this bid, including what was refused — the refusals are the point."""
    rows = (
        session.execute(
            select(Document)
            .where(Document.bid_id == context.bid.id)
            .order_by(Document.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [DocumentOut.model_validate(row) for row in rows]


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(context: CurrentBid, session: DbSession, document_id: uuid.UUID) -> DocumentOut:
    document = session.get(Document, document_id)
    if document is None or document.bid_id != context.bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    return DocumentOut.model_validate(document)


@router.get("/{document_id}/content")
def download(context: CurrentBid, session: DbSession, document_id: uuid.UUID) -> dict[str, str]:
    """A short-lived link to the original.

    A quarantined file is never handed out: the whole point of quarantine is that nothing
    downstream — including a browser — opens it.
    """
    document = session.get(Document, document_id)
    if document is None or document.bid_id != context.bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    if document.state == "quarantined":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"this file is quarantined ({document.rejected_reason}) and cannot be downloaded",
        )
    if document.state == "awaiting_scan":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "this file has not been scanned yet; try again shortly"
        )
    url = get_object_store().presigned_url(document.storage_key, expires_in=900)
    return {"url": url, "expires_in": "900"}


@router.post("/rescan", response_model=dict[str, int])
def rescan(
    context: CurrentBid,
    session: DbSession,
    _: Annotated[Principal, require(Action.DOCUMENT_UPLOAD)],
) -> dict[str, int]:
    """Retry the files an outage held. Safe to call repeatedly."""
    moved = rescan_held(session, get_object_store(), get_scanner(), context.bid.id)
    return {"moved": moved}
