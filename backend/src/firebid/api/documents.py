"""Uploading a tender set, and seeing what became of every file (FR-DOC-01).

Two ways in, because a 300-sheet set and a single addendum are different problems:

* `POST /bids/{bid_id}/documents` takes files directly. Simple, and right up to a few hundred
  megabytes.
* `POST /bids/{bid_id}/documents/uploads` hands back a presigned URL per file so the browser
  puts the bytes straight into object storage, then `.../uploads/{key}/complete` registers
  what landed. A dropped connection costs one file, not the whole set.

The response always accounts for every file: what was stored, what was a duplicate, what was
refused and why. A silent omission is the failure this endpoint exists to prevent.

A whole folder is sent as files with their paths in it (FR-DOC-09), in as many requests as
it takes. Each file has an origin (FR-DOC-10): the person sending it says which of its
folders are the client's tender documents, the company's own working documents, or
reference; `POST .../origins` proposes that from the paths. Only tender documents are read.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.api.deps import BidContext, CurrentBid, DbSession, require
from firebid.auth.permissions import Action
from firebid.auth.provisioning import Principal
from firebid.db.models.documents import Document
from firebid.ingest.origin import Origin, propose
from firebid.ingest.scanning import get_scanner
from firebid.services.ingestion import (
    Ingestor,
    IngestOutcome,
    checksum,
    existing_document,
    readable,
    set_origin,
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
    source_path: str | None = None
    origin: str = "tender"
    origin_status: str = "confirmed"
    origin_reason: str | None = None

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
    # Not documents (an office lock file, a thumbnail cache): reported, never stored.
    ignored: list[RefusedOut] = []
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
        ignored=[RefusedOut(filename=n, reason=r) for n, r in outcome.ignored],
        accounted_for=outcome.accounted_for,
    )


# What the parse job reads. A legacy .doc or .xls is not here: its converted copy is, and
# the original takes the copy's classification.
PARSEABLE_KINDS = frozenset({"pdf", "dxf", "xlsx", "docx"})


def _queue_parsing(session: Session, report: UploadReport, user_id: uuid.UUID) -> None:
    """Queue a parse job for each newly stored document the pipeline can read.

    In the caller's transaction (the `jobs` convention): a rollback takes the jobs with it,
    so a job never runs against a document row that was never committed. The job acts as
    whoever sent or released the file, since row-level security shows it nothing otherwise.
    """
    from firebid.jobs.enqueue import enqueue
    from firebid.jobs.tasks import parse_document

    for document in report.stored:
        # Only a tender document whose origin is confirmed is read (FR-DOC-10).
        if document.origin != "tender" or document.origin_status != "confirmed":
            continue
        if document.kind in PARSEABLE_KINDS:
            enqueue(session, parse_document, document_id=str(document.id), user_id=str(user_id))


def _merge(into: UploadReport, addition: UploadReport) -> None:
    into.stored += addition.stored
    into.duplicates += addition.duplicates
    into.rejected += addition.rejected
    into.quarantined += addition.quarantined
    into.awaiting_scan += addition.awaiting_scan
    into.ignored += addition.ignored
    into.accounted_for += addition.accounted_for


def _ingestor(
    session: Session, context: BidContext, addendum_id: uuid.UUID | None = None
) -> Ingestor:
    return Ingestor(
        session,
        get_object_store(),
        get_scanner(),
        bid_id=context.bid.id,
        created_by_id=context.principal.user_id,
        created_by=context.principal.display_name,
        tender_package_id=_package_of(session, context, addendum_id),
    )


def _package_of(
    session: Session, context: BidContext, addendum_id: uuid.UUID | None
) -> uuid.UUID | None:
    """The tender package of the addendum an upload belongs to (FR-DOC-05)."""
    if addendum_id is None:
        return None
    from firebid.db.models.documents import Addendum

    addendum = session.get(Addendum, addendum_id)
    if addendum is None or addendum.bid_id != context.bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "addendum not found")
    return addendum.tender_package_id


def _size_of(upload_file: UploadFile) -> int:
    """An uploaded file's size, without reading it."""
    if upload_file.size is not None:
        return upload_file.size
    end = upload_file.file.seek(0, 2)
    upload_file.file.seek(0)
    return end


@router.post("", response_model=UploadReport, status_code=status.HTTP_201_CREATED)
def upload(
    context: CurrentBid,
    session: DbSession,
    _: Annotated[Principal, require(Action.DOCUMENT_UPLOAD)],
    files: Annotated[list[UploadFile], File()],
    addendum_id: Annotated[uuid.UUID | None, Form()] = None,
    paths: Annotated[list[str] | None, Form()] = None,
    origins: Annotated[list[str] | None, Form()] = None,
) -> UploadReport:
    """Upload one or more files, or one archive holding a whole set.

    With `addendum_id`, the files are that addendum's: every revision read from them is
    linked to it, and its date orders them against what they replace.

    For a folder, `paths` gives each file's path in it and `origins` what the person said
    each is, one per file and in the files' order. A file with no origin given has one
    proposed from its path; anything proposed as other than a tender document is kept
    unread until a person confirms it.
    """
    for name, given in (("paths", paths), ("origins", origins)):
        if given is not None and len(given) != len(files):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"'{name}' must have one entry for each file, in the same order",
            )
    try:
        said = [Origin(value) if value else None for value in origins or []]
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    # Sizes first, from the files as they were received: a file over the limit is refused
    # before it is read into memory, and before any of its neighbours is stored.
    for upload_file in files:
        if _size_of(upload_file) > MAX_DIRECT_UPLOAD_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"'{upload_file.filename}' is larger than "
                f"{MAX_DIRECT_UPLOAD_BYTES // (1024 * 1024)} MB; use the presigned upload route",
            )
    ingestor = _ingestor(session, context, addendum_id)
    report = UploadReport()

    for index, upload_file in enumerate(files):
        # One file at a time: the last one's bytes are let go before the next is read.
        payload = upload_file.file.read()
        outcome = ingestor.ingest(
            upload_file.filename or "unnamed",
            payload,
            source_path=paths[index] if paths else None,
            origin=said[index] if said else None,
        )
        _merge(report, _report(outcome))

    _queue_parsing(session, report, context.principal.user_id)
    return report


class OriginProposal(BaseModel):
    path: str
    origin: Literal["tender", "working", "reference", "ignored"]
    reason: str


@router.post("/origins", response_model=list[OriginProposal])
def propose_origins(
    context: CurrentBid,
    _: Annotated[Principal, require(Action.DOCUMENT_UPLOAD)],
    body: Annotated[list[Annotated[str, Field(max_length=1024)]], Field(max_length=20_000)],
) -> list[OriginProposal]:
    """Whose document each path looks like, before anything is sent (FR-DOC-10).

    A proposal only: the person sending the folder confirms or changes it, and what they
    say is what each file is stored with.
    """
    answers = []
    for path in body:
        proposal = propose(path)
        answers.append(
            OriginProposal(path=path, origin=str(proposal.origin), reason=proposal.reason)
        )
    return answers


class OriginChange(BaseModel):
    document_ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=5000)]
    origin: Literal["tender", "working", "reference"]
    reason: str | None = Field(default=None, max_length=500)


class OriginChanged(BaseModel):
    changed: list[uuid.UUID]
    refused: dict[uuid.UUID, str]


@router.post("/origin", response_model=OriginChanged)
def change_origin(
    body: OriginChange,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.DOCUMENT_REVIEW)],
) -> OriginChanged:
    """Say whose documents these are. One that becomes a tender document is queued to be read."""
    from firebid.jobs.enqueue import enqueue
    from firebid.jobs.tasks import parse_document

    outcome = OriginChanged(changed=[], refused={})
    for document_id in body.document_ids:
        document = session.get(Document, document_id)
        if document is None or document.bid_id != context.bid.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
        try:
            read_it = set_origin(
                session, document, Origin(body.origin), principal.actor(), body.reason
            )
        except ValueError as refusal:
            outcome.refused[document_id] = str(refusal)
            continue
        outcome.changed.append(document_id)
        if read_it and readable(document) and document.kind in PARSEABLE_KINDS:
            enqueue(
                session,
                parse_document,
                document_id=str(document.id),
                user_id=str(principal.user_id),
            )
    return outcome


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


@router.post("/read-again", response_model=dict[str, int])
def read_again(
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.DOCUMENT_UPLOAD)],
) -> dict[str, int]:
    """Read again what could not be read: sheets that failed, drawings refused after they
    were scanned, and views found by an older detector (`progress.read_again` counts them).

    Safe to call repeatedly: what is queued is being read, so asking again finds nothing.
    """
    from firebid.services import parse_pipeline

    return parse_pipeline.read_again(session, context.bid.id, principal.actor())


@router.post("/rescan", response_model=dict[str, int])
def rescan(
    context: CurrentBid,
    session: DbSession,
    _: Annotated[Principal, require(Action.DOCUMENT_UPLOAD)],
) -> dict[str, int]:
    """Retry the files an outage held, and queue the released ones to be read.

    Safe to call repeatedly: a file is released once, so it is queued once.
    """
    outcome = _ingestor(session, context).release_held()
    _queue_parsing(session, _report(outcome), context.principal.user_id)
    return {"moved": outcome.moved}
