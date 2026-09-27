"""Proposing each document's type (FR-DOC-02): rules in the sandbox, the model on the worker.

Split the same way as title blocks. `classify_in_sandbox` opens the file, so it runs beside
the parse job; it extracts a digest, applies the rules, and stores the answer as a proposal.
When the rules are not sure, `classify_with_model` runs on the ordinary worker with only the
stored digest, and anything the model is not sure of becomes a review task for a person.

A legacy `.doc` or `.xls` is never classified itself: its converted copy is read instead, and
the original takes the copy's answer, since they are the same document.
"""

from __future__ import annotations

from typing import Any, cast

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from firebid.db.models.documents import Document, DocumentRevision, Sheet, SheetRevision
from firebid.drawings.title_block import DEFAULT_THRESHOLD
from firebid.ingest.classification import Classification, Digest, DocType, classify
from firebid.sandbox.runner import SandboxFailure, run_sandboxed

log = structlog.get_logger("firebid.classification")

# Kinds whose content the sandbox can digest. A DXF needs no digest: it is a drawing.
DIGESTIBLE = frozenset({"pdf", "docx", "xlsx"})
# How much of the digest is kept beside the document, for the model and for a reviewer.
KEPT_TEXT = 4_000


def classify_in_sandbox(session: Session, document: Document, payload: bytes) -> Classification:
    """Apply the rules to one document and store the proposal. Queues the model if unsure."""
    raw: dict[str, Any] = {"text": "", "pages": 0, "sheet_names": [], "header_rows": []}
    if document.kind in DIGESTIBLE:
        from firebid.parsing import digest as digest_parsing

        try:
            raw = run_sandboxed(digest_parsing.digest, payload, document.kind or "")
        except SandboxFailure as failure:
            log.warning("digest_failed", document_id=str(document.id), reason=failure.reason)
            raw = {**raw, "error": failure.reason}

    sheets, identified = _sheet_counts(session, document)
    digest = Digest(
        kind=document.kind or "",
        filename=document.filename,
        text=str(raw.get("text") or ""),
        sheet_names=tuple(raw.get("sheet_names") or ()),
        header_rows=tuple(tuple(row) for row in raw.get("header_rows") or ()),
        pages=int(raw.get("pages") or 0),
        identified_sheets=identified,
        sheets=sheets,
    )
    result = classify(digest)
    _record(document, result, digest)
    _pass_to_original(session, document)
    register_document(session, document)

    if not result.settled:
        from firebid.jobs.enqueue import enqueue
        from firebid.jobs.tasks import classify_document_with_model

        enqueue(
            session,
            classify_document_with_model,
            document_id=str(document.id),
            user_id=str(document.created_by_id) if document.created_by_id else "",
        )
    session.flush()
    log.info(
        "document_classified",
        document_id=str(document.id),
        doc_type=str(result.doc_type),
        confidence=result.confidence,
        settled=result.settled,
    )
    return result


def _sheet_counts(session: Session, document: Document) -> tuple[int, int]:
    """How many sheets this document became, and how many had a title block read well."""
    sheets = session.execute(
        select(func.count()).select_from(Sheet).where(Sheet.document_id == document.id)
    ).scalar_one()
    identified = session.execute(
        select(func.count())
        .select_from(SheetRevision)
        .join(Sheet, Sheet.id == SheetRevision.sheet_id)
        .where(
            Sheet.document_id == document.id,
            SheetRevision.source_confidence >= DEFAULT_THRESHOLD,
        )
    ).scalar_one()
    return int(sheets), int(identified)


def _record(document: Document, result: Classification, digest: Digest | None) -> None:
    document.doc_type = str(result.doc_type)
    document.doc_type_confidence = result.confidence
    kept = dict(document.classification or {})
    kept.update({"method": result.method, "reasons": list(result.reasons)})
    if digest is not None:
        kept["digest"] = {
            "text": digest.text[:KEPT_TEXT],
            "sheet_names": list(digest.sheet_names),
            "header_rows": [list(row) for row in digest.header_rows[:10]],
        }
    document.classification = kept


def _pass_to_original(session: Session, document: Document) -> None:
    """A converted copy's type is its original's: they are the same document."""
    if document.derived_from_id is None:
        return
    original = session.get(Document, document.derived_from_id)
    if original is not None:
        original.doc_type = document.doc_type
        original.doc_type_confidence = document.doc_type_confidence
        original.classification = {"method": "converted copy", "copy_id": str(document.id)}
        if original.state == "received":
            original.state = "done"  # read through its copy; nothing more will happen to it


# --- The model, on the ordinary worker -------------------------------------------------------


def classify_with_model(session: Session, document: Document, router: Any) -> Document:
    """Ask `document_classifier` about a document the rules could not place confidently."""
    from firebid.agents.base import AgentInput
    from firebid.agents.doc_classifier import DocumentClassifier, DocumentDigest, DocumentType
    from firebid.agents.runtime import Escalated, run_agent_with_result

    kept = dict(document.classification or {})
    digest = cast(dict[str, Any], kept.get("digest") or {})
    request = AgentInput(
        bid_id=document.bid_id,
        idempotency_key=f"doc_classify:{document.id}",
        payload=DocumentDigest(
            filename=document.filename,
            kind=document.kind or "",
            text=str(digest.get("text") or ""),
            sheet_names=[str(name) for name in digest.get("sheet_names") or []],
            header_rows=[[str(cell) for cell in row] for row in digest.get("header_rows") or []],
            rules_said=document.doc_type,
        ),
    )
    try:
        run, result = run_agent_with_result(session, DocumentClassifier(router), request)
    except Escalated as escalation:
        document.classification = {**kept, "review_task_id": str(escalation.task.id)}
        session.flush()
        return document

    if result is None or not isinstance(result.output, DocumentType):
        return document  # a redelivery: the first delivery recorded the answer
    answer = result.output
    _record(
        document,
        Classification(answer.doc_type, answer.confidence, "model", (answer.reason,)),
        None,
    )
    register_document(session, document)
    document.classification = {**(document.classification or {}), "agent_run_id": str(run.id)}
    _pass_to_original(session, document)
    session.flush()
    return document


def confirm(document: Document, doc_type: DocType, person: str) -> None:
    """A person's answer: the only thing that turns a proposal into a decision."""
    _record(document, Classification(doc_type, 1.0, "person", (f"confirmed by {person}",)), None)


# Kinds that are originals of a converted copy: the copy is the register's row, not these.
LEGACY = frozenset({"xls", "doc"})


def register_document(session: Session, document: Document) -> DocumentRevision | None:
    """Put a non-drawing document in the specification register (FR-DOC-03).

    Once per document; a later classification only updates its type. A drawing is registered
    by its sheets instead, and a legacy original through its converted copy.
    """
    from firebid.ingest.document_identity import filename_revision, identify, text_revision
    from firebid.services.revisions import settle_document

    if document.doc_type == str(DocType.DRAWING) or document.kind in LEGACY:
        return None
    existing = session.execute(
        select(DocumentRevision).where(DocumentRevision.document_id == document.id)
    ).scalar_one_or_none()
    if existing is not None:
        existing.doc_type = document.doc_type
        _read_if_specification(session, document)
        return existing

    digest = cast(dict[str, Any], (document.classification or {}).get("digest") or {})
    text = str(digest.get("text") or "")
    identity = identify(document.filename, text)
    from firebid.services.addenda import addendum_for_document

    addendum = addendum_for_document(session, document)
    revision = DocumentRevision(
        bid_id=document.bid_id,
        document_id=document.id,
        addendum_id=addendum.id if addendum is not None else None,
        doc_type=document.doc_type,
        doc_key=identity.key,
        title=identity.title,
        revision_label=identity.revision,
        sources={"text": text_revision(text), "filename": filename_revision(document.filename)},
        created_by_id=document.created_by_id,
    )
    session.add(revision)
    session.flush()
    if identity.key is None:
        from firebid.services.revisions import revision_task

        revision_task(session, revision, "document_identity", "Say what this document is.")
    else:
        settle_document(session, revision)
    _read_if_specification(session, document)
    return revision


def _read_if_specification(session: Session, document: Document) -> None:
    """A specification is read for its attributes once registered (P1-06)."""
    from firebid.services.specs import queue_reading

    session.flush()
    queue_reading(session, document)
