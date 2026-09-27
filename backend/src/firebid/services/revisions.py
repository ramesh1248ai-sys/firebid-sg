"""Revision control: register proposals, keep exactly one Current, flag Conflict (FR-DOC-04).

Every state change goes through the sheet revision state machine (`apply_transition`), so the
guard on "one Current per drawing number" and the audit trail apply to all of it. The platform
may register, mark current, supersede and flag a conflict on its own; only a person resolves
a conflict, restores a superseded revision or withdraws one, because those undo a decision.

`current_sheets` is the only query takeoff may use (guardrail 6).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

import structlog
from sqlalchemy import select
from sqlalchemy.orm import InstrumentedAttribute, Session

from firebid.db.models.core import Bid, Project
from firebid.db.models.documents import (
    Addendum,
    Document,
    DocumentRevision,
    Sheet,
    SheetRevision,
    TransmittalEntry,
)
from firebid.db.models.workflow import HumanTask
from firebid.domain.actors import SYSTEM_ACTOR, Actor
from firebid.domain.state_machines import SheetRevisionState
from firebid.drawings.revisions import (
    Candidate,
    Scheme,
    compare,
    latest,
    reconcile,
    revision_from_filename,
    scheme_named,
)
from firebid.drawings.title_block import DEFAULT_THRESHOLD
from firebid.sandbox.runner import SandboxFailure, run_sandboxed
from firebid.services.transitions import apply_transition

log = structlog.get_logger("firebid.revisions")

State = SheetRevisionState
# The states a revision competes for Current from. Conflict and Withdrawn are out of it.
IN_THE_RUNNING = (str(State.REGISTERED), str(State.CURRENT), str(State.SUPERSEDED))

# Why a revision is in Conflict: its sources disagree about its label, or its label cannot
# be ordered against the others. The first is about the file; the second about the set.
CONFLICT_SOURCES = "sources"
CONFLICT_ORDER = "order"

# Drawings and documents follow one revision model (requirements §7). What differs is what
# identifies the thing being revised: a drawing number, or a document's key.
Revision = SheetRevision | DocumentRevision
RevisionModel = type[SheetRevision] | type[DocumentRevision]


def _key_column(model: RevisionModel) -> InstrumentedAttribute[str | None]:
    return model.sheet_number if model is SheetRevision else model.doc_key  # type: ignore[union-attr]


def _key(revision: Revision) -> str | None:
    return revision.sheet_number if isinstance(revision, SheetRevision) else revision.doc_key


def scheme_for(session: Session, bid_id: uuid.UUID) -> Scheme:
    name = session.execute(
        select(Project.revision_scheme)
        .join(Bid, Bid.project_id == Project.id)
        .where(Bid.id == bid_id)
    ).scalar_one_or_none()
    return scheme_named(name)


def settle(session: Session, revision: SheetRevision, actor: Actor = SYSTEM_ACTOR) -> None:
    """Take an identified proposal as far as the evidence allows.

    A proposal waits in `received` while its number or revision is unknown or not confident
    enough; a person or the model check comes back to it. Once identified, its sources are
    compared: disagreement is a Conflict, agreement registers it and the Current revision of
    its drawing number is worked out again.
    """
    if revision.state != str(State.RECEIVED):
        return
    confirmed = revision.extraction_method == "person"
    if not revision.sheet_number or not revision.revision_label:
        return
    if not confirmed and (revision.source_confidence or 0.0) < DEFAULT_THRESHOLD:
        return

    agreement = _check_sources(session, revision)
    if agreement.conflict:
        _flag(session, revision, actor, agreement.conflict, CONFLICT_SOURCES)
        return
    apply_transition(
        session, revision, target=State.REGISTERED, actor=actor, reason="title block read"
    )
    recompute_current(session, revision.bid_id, revision.sheet_number, actor)


def settle_document(
    session: Session, revision: DocumentRevision, actor: Actor = SYSTEM_ACTOR
) -> None:
    """Register a document revision and work out the Current one of its document.

    A document with no identity waits for a person. One whose own text and file name give
    different revisions is a Conflict, like a drawing's.
    """
    if revision.state != str(State.RECEIVED) or not revision.doc_key:
        return
    sources = revision.sources or {}
    said_text = sources.get("text")
    said_name = sources.get("filename")
    agreement = reconcile(
        str(said_text) if said_text else None,
        str(said_name) if said_name else None,
        None,
        first="document",
    )
    if agreement.conflict:
        _flag(session, revision, actor, agreement.conflict, CONFLICT_SOURCES)
        return
    apply_transition(
        session, revision, target=State.REGISTERED, actor=actor, reason="document classified"
    )
    recompute_current(session, revision.bid_id, revision.doc_key, actor, model=DocumentRevision)


@dataclass(frozen=True)
class _Sources:
    conflict: str | None


def _check_sources(session: Session, revision: SheetRevision) -> _Sources:
    document = _document_of(session, revision)
    filename = (
        revision_from_filename(document.filename, revision.sheet_number or "")
        if document is not None
        else None
    )
    transmittal = _transmittal_says(session, revision, document)
    if revision.extraction_method == "person":
        # A person's reading is the decision; the others are recorded, not voted against it.
        agreement = reconcile(revision.revision_label, None, None)
    else:
        agreement = reconcile(revision.revision_label, filename, transmittal)
    revision.sources = {
        **(revision.sources or {}),
        "title_block": revision.revision_label,
        "filename": filename,
        "transmittal": transmittal,
    }
    return _Sources(agreement.conflict)


def _document_of(session: Session, revision: SheetRevision) -> Document | None:
    sheet = session.get(Sheet, revision.sheet_id)
    return session.get(Document, sheet.document_id) if sheet is not None else None


def _entries_for(
    session: Session, bid_id: uuid.UUID, sheet_number: str, package_id: uuid.UUID | None
) -> list[TransmittalEntry]:
    """The transmittal entries that accompany a file: those of the same tender package."""
    query = select(TransmittalEntry).where(
        TransmittalEntry.bid_id == bid_id, TransmittalEntry.sheet_number == sheet_number
    )
    query = query.where(
        TransmittalEntry.tender_package_id == package_id
        if package_id is not None
        else TransmittalEntry.tender_package_id.is_(None)
    )
    return list(session.execute(query).scalars())


def _transmittal_says(
    session: Session, revision: SheetRevision, document: Document | None
) -> str | None:
    """The revision the accompanying transmittal gives this drawing, if it lists it."""
    entries = _entries_for(
        session,
        revision.bid_id,
        revision.sheet_number or "",
        document.tender_package_id if document is not None else None,
    )
    if not entries:
        return None
    if any(entry.revision_label == revision.revision_label for entry in entries):
        return revision.revision_label
    # It lists the drawing at another revision: the latest it lists is what it says.
    listed = sorted(entries, key=lambda entry: (entry.issued_on or date.min, entry.created_at))
    return listed[-1].revision_label


def _issued(session: Session, revision: Revision) -> date | None:
    """When a revision was issued, for breaking a tie the scheme cannot.

    The addendum's date first, then the transmittal's: those are when the revision reached
    the tenderer, which is what decides which one they must price. The title block's own date
    only when neither exists, because consultants often leave it at the date of first issue.
    """
    if revision.addendum_id is not None:
        issued = session.execute(
            select(Addendum.issued_on).where(Addendum.id == revision.addendum_id)
        ).scalar_one_or_none()
        if issued is not None:
            return issued
    if isinstance(revision, SheetRevision):
        transmitted = session.execute(
            select(TransmittalEntry.issued_on)
            .where(
                TransmittalEntry.bid_id == revision.bid_id,
                TransmittalEntry.sheet_number == revision.sheet_number,
                TransmittalEntry.revision_label == revision.revision_label,
                TransmittalEntry.issued_on.is_not(None),
            )
            .limit(1)
        ).scalar_one_or_none()
        if transmitted is not None:
            return transmitted
    return revision.revision_date


def recompute_current(
    session: Session,
    bid_id: uuid.UUID,
    sheet_number: str | None,
    actor: Actor = SYSTEM_ACTOR,
    *,
    model: RevisionModel = SheetRevision,
) -> Revision | None:
    """Make the latest revision of a drawing (or document) Current and supersede the rest.

    When the scheme and the dates cannot say which is latest, nothing is guessed: the
    revisions still competing go to Conflict, the Current one included, so takeoff stops
    using this drawing until a person decides.
    """
    if not sheet_number:
        return None
    rows: list[Revision] = cast(
        list[Revision],
        list(
            session.execute(
                select(model).where(
                    model.bid_id == bid_id,
                    _key_column(model) == sheet_number,
                    model.state.in_(IN_THE_RUNNING),
                )
            ).scalars()
        ),
    )
    if not rows:
        return None
    by_key = {str(row.id): row for row in rows}
    scheme = scheme_for(session, bid_id)
    winner = latest(
        [Candidate(str(row.id), row.revision_label or "", _issued(session, row)) for row in rows],
        scheme,
    )

    if winner is None:
        labels = ", ".join(sorted(row.revision_label or "?" for row in rows))
        why = (
            f"Revisions {labels} of {sheet_number} cannot be put in order by the "
            f"'{scheme.name}' scheme or by their dates. Say which is current."
        )
        for row in rows:
            if row.state in (str(State.REGISTERED), str(State.CURRENT)):
                _flag(session, row, actor, why, CONFLICT_ORDER)
        return None

    chosen = by_key[winner.key]
    if chosen.state == str(State.SUPERSEDED):
        # Restoring a superseded revision undoes a decision; that is a person's to make.
        revision_task(
            session,
            chosen,
            "revision_restore",
            f"{sheet_number} {chosen.revision_label} is now the latest revision but was "
            "superseded. Restore it as current if that is right.",
        )
        return None

    for row in rows:
        if row is chosen:
            continue
        if row.state in (str(State.CURRENT), str(State.REGISTERED)):
            apply_transition(
                session,
                row,
                target=State.SUPERSEDED,
                actor=actor,
                reason=f"superseded by {chosen.revision_label}",
            )
            row.superseded_by_id = chosen.id
    if chosen.state != str(State.CURRENT):
        session.flush()  # the old Current has left, so the guard and the index allow this one
        apply_transition(
            session, chosen, target=State.CURRENT, actor=actor, reason="latest revision"
        )
    session.flush()
    log.info("revision_current", sheet_number=sheet_number, revision=chosen.revision_label)
    return chosen


def _flag(session: Session, revision: Revision, actor: Actor, why: str, kind: str) -> None:
    apply_transition(session, revision, target=State.CONFLICT, actor=actor, reason=why)
    revision.conflict_reason = why
    revision.sources = {**(revision.sources or {}), "conflict": kind}
    revision_task(session, revision, "revision_conflict", why)
    log.warning("revision_conflict", revision_id=str(revision.id), kind=kind)


def _payload_key(revision: Revision) -> str:
    return f"{type(revision).__tablename__}_id"


def revision_task(session: Session, revision: Revision, kind: str, why: str) -> HumanTask:
    task = HumanTask(
        bid_id=revision.bid_id,
        kind=kind,
        title=f"{_key(revision) or 'An unidentified item'} {revision.revision_label or ''}: "
        + ("resolve the revision conflict" if kind == "revision_conflict" else "check"),
        required_role="estimator",
        payload={_payload_key(revision): str(revision.id), "why": why},
    )
    session.add(task)
    session.flush()
    return task


def resolve_conflict(
    session: Session,
    revision: Revision,
    *,
    outcome: SheetRevisionState,
    actor: Actor,
    reason: str,
    revision_label: str | None = None,
) -> None:
    """A person's decision on a revision in Conflict: Current, Superseded or Withdrawn.

    `revision_label` corrects the label when the sources disagreed and the person has
    decided which is right. Resolving one revision as Current supersedes the Current one and
    settles the others that were in Conflict only because they could not be ordered.
    """
    if revision.state != str(State.CONFLICT):
        raise ValueError("only a revision in Conflict can be resolved")
    if revision_label:
        revision.revision_label = revision_label.strip().upper()
        revision.sources = {**(revision.sources or {}), "person": revision.revision_label}

    model = type(revision)
    others: list[Revision] = cast(
        list[Revision],
        list(
            session.execute(
                select(model).where(
                    model.bid_id == revision.bid_id,
                    _key_column(model) == _key(revision),
                    model.id != revision.id,
                )
            ).scalars()
        ),
    )
    if outcome is State.CURRENT:
        for other in others:
            if other.state == str(State.CURRENT):
                apply_transition(
                    session, other, target=State.SUPERSEDED, actor=actor, reason=reason
                )
                other.superseded_by_id = revision.id
        session.flush()
    elif outcome is State.SUPERSEDED:
        current = next((other for other in others if other.state == str(State.CURRENT)), None)
        revision.superseded_by_id = current.id if current is not None else None

    apply_transition(session, revision, target=outcome, actor=actor, reason=reason)
    revision.conflict_reason = None
    _close_tasks(session, revision, actor)

    if outcome is State.CURRENT:
        for other in others:
            ordering_only = (other.sources or {}).get("conflict") == CONFLICT_ORDER
            if other.state == str(State.CONFLICT) and ordering_only:
                apply_transition(
                    session,
                    other,
                    target=State.SUPERSEDED,
                    actor=actor,
                    reason=f"resolved with {revision.revision_label}: {reason}",
                )
                other.superseded_by_id = revision.id
                other.conflict_reason = None
                _close_tasks(session, other, actor)
    session.flush()


def _close_tasks(session: Session, revision: Revision, actor: Actor) -> None:
    from datetime import UTC, datetime

    for task in session.execute(
        select(HumanTask).where(
            HumanTask.bid_id == revision.bid_id,
            HumanTask.state == "open",
            HumanTask.kind.in_(("revision_conflict", "revision_restore")),
        )
    ).scalars():
        if task.payload.get(_payload_key(revision)) == str(revision.id):
            task.state = "done"
            task.completed_at = datetime.now(UTC)
            task.completed_by_id = actor.id


def current_sheets(session: Session, bid_id: uuid.UUID) -> list[tuple[SheetRevision, Sheet]]:
    """The sheets takeoff may use: the Current revision of each drawing (guardrail 6).

    Nothing else in the platform decides which sheets are measured. A drawing in Conflict has
    no Current revision, so it is simply absent until a person resolves it.
    """
    rows = session.execute(
        select(SheetRevision, Sheet)
        .join(Sheet, Sheet.id == SheetRevision.sheet_id)
        .where(SheetRevision.bid_id == bid_id, SheetRevision.state == str(State.CURRENT))
        .order_by(SheetRevision.sheet_number)
    ).all()
    return [(revision, sheet) for revision, sheet in rows]


# --- Transmittals ----------------------------------------------------------------------------


def read_transmittal(session: Session, document: Document, payload: bytes) -> int:
    """Store what a drawing list or transmittal lists, and check the drawings against it.

    Files in one upload are read in no particular order, so the drawings a transmittal lists
    may already be registered, or even Current. Those are checked again now.
    """
    from firebid.parsing.transmittal import transmittal_rows

    try:
        rows: list[dict[str, Any]] = run_sandboxed(transmittal_rows, payload)
    except SandboxFailure as failure:
        log.warning("transmittal_unreadable", document_id=str(document.id), reason=failure.reason)
        return 0
    if not rows:
        return 0

    stored = 0
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row["sheet_number"], row["revision"])
        if key in seen:
            continue
        seen.add(key)
        exists = session.execute(
            select(TransmittalEntry.id).where(
                TransmittalEntry.document_id == document.id,
                TransmittalEntry.sheet_number == key[0],
                TransmittalEntry.revision_label == key[1],
            )
        ).scalar_one_or_none()
        if exists is not None:
            continue
        session.add(
            TransmittalEntry(
                bid_id=document.bid_id,
                document_id=document.id,
                tender_package_id=document.tender_package_id,
                sheet_number=key[0][:120],
                revision_label=key[1][:40],
                issued_on=date.fromisoformat(row["issued_on"]) if row.get("issued_on") else None,
                title=(row.get("title") or None) and str(row["title"])[:300],
            )
        )
        stored += 1
    session.flush()
    _recheck(session, document, {number for number, _ in seen})
    log.info("transmittal_read", document_id=str(document.id), entries=stored)
    return stored


def _recheck(session: Session, transmittal: Document, numbers: set[str]) -> None:
    """Check revisions already on the bid against a transmittal that arrived after them."""
    for revision in session.execute(
        select(SheetRevision).where(
            SheetRevision.bid_id == transmittal.bid_id,
            SheetRevision.sheet_number.in_(numbers),
        )
    ).scalars():
        if revision.state == str(State.RECEIVED):
            settle(session, revision)
            continue
        if revision.state not in (str(State.REGISTERED), str(State.CURRENT)):
            continue
        document = _document_of(session, revision)
        if document is None or document.tender_package_id != transmittal.tender_package_id:
            continue
        says = _transmittal_says(session, revision, document)
        agreement = reconcile(revision.revision_label, None, says)
        revision.sources = {**(revision.sources or {}), "transmittal": says}
        if agreement.conflict:
            _flag(session, revision, SYSTEM_ACTOR, agreement.conflict, CONFLICT_SOURCES)
    for number in numbers:
        recompute_current(session, transmittal.bid_id, number)


def ordered(labels: list[str], scheme: Scheme) -> list[str] | None:
    """Labels in issue order, or None when the scheme cannot order them all."""
    from functools import cmp_to_key

    def by_scheme(a: str, b: str) -> int:
        order = compare(a, b, scheme)
        if order is None:
            raise ValueError(f"cannot order {a} and {b}")
        return order

    try:
        return sorted(labels, key=cmp_to_key(by_scheme))
    except ValueError:
        return None
