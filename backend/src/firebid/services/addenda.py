"""Addenda and clarification responses, and what each one changed (FR-DOC-05).

An addendum is registered with its number and date and gets a tender package of its own. Files
uploaded "as part of Addendum 2" go into that package, and every sheet or document revision
read from them is linked to the addendum. That link does three jobs: the register shows which
addendum brought each revision; the addendum's date orders revisions whose labels cannot be
ordered; and `affected_items` can answer "what did Addendum 2 change?".

`affected_items` answers for sheets and documents here. Takeoff, the BOQ and the
clarification candidates extend it by registering providers (`services.delta`, P2-02), so
the answer grows with the platform instead of each step writing its own version of the
question.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from firebid.db.models.core import TenderPackage
from firebid.db.models.documents import (
    Addendum,
    Document,
    DocumentRevision,
    Sheet,
    SheetRevision,
)
from firebid.domain.actors import Actor

log = structlog.get_logger("firebid.addenda")


class AddendumExists(Exception):
    pass


def register_addendum(
    session: Session,
    bid_id: uuid.UUID,
    *,
    number: str,
    issued_on: date | None,
    summary: str | None,
    actor: Actor,
) -> Addendum:
    """Record an addendum, with a package its files will be uploaded into."""
    number = number.strip()
    if session.execute(
        select(Addendum.id).where(Addendum.bid_id == bid_id, Addendum.number == number)
    ).scalar_one_or_none():
        raise AddendumExists(f"Addendum {number} is already registered on this bid")
    package = TenderPackage(
        bid_id=bid_id, name=f"Addendum {number}", received_on=issued_on, created_by_id=actor.id
    )
    session.add(package)
    session.flush()
    addendum = Addendum(
        bid_id=bid_id,
        number=number,
        issued_on=issued_on,
        tender_package_id=package.id,
        summary=summary,
        created_by_id=actor.id,
    )
    session.add(addendum)
    session.flush()
    # The takeoff as it stands before the addendum's sheets arrive: what its delta report
    # is made against (FR-QTO-12). Nothing to keep when nothing has been taken off yet.
    from firebid.services import delta, qto

    if qto.live_items(session, bid_id):
        delta.take_snapshot(session, bid_id, f"before Addendum {number}", addendum_id=addendum.id)
    log.info("addendum_registered", bid_id=str(bid_id), number=number)
    return addendum


def addendum_for_document(session: Session, document: Document) -> Addendum | None:
    """The addendum a document arrived in, if it arrived in one."""
    if document.tender_package_id is None:
        return None
    return session.execute(
        select(Addendum).where(Addendum.tender_package_id == document.tender_package_id)
    ).scalar_one_or_none()


# --- What an addendum changed ----------------------------------------------------------------


@dataclass(frozen=True)
class AffectedItem:
    """One thing an addendum changed: what it is, what it replaced, and where it stands."""

    kind: str  # sheet | document, and later qto_item, boq_line
    id: uuid.UUID
    reference: str  # drawing number, document key, item number
    revision: str | None
    state: str
    replaces: str | None = None  # the revision it superseded, if any
    detail: dict[str, Any] = field(default_factory=dict)


# A provider adds the items of one kind that an addendum affected. Later steps register theirs.
Provider = Callable[[Session, Addendum], list[AffectedItem]]
PROVIDERS: list[Provider] = []


def provides_affected_items(provider: Provider) -> Provider:
    """Register a provider: `affected_items` will include what it returns."""
    PROVIDERS.append(provider)
    return provider


def affected_items(session: Session, addendum: Addendum) -> list[AffectedItem]:
    from firebid.services import delta  # noqa: F401  (registers its providers)

    items: list[AffectedItem] = []
    for provider in PROVIDERS:
        items.extend(provider(session, addendum))
    return items


@provides_affected_items
def _sheets(session: Session, addendum: Addendum) -> list[AffectedItem]:
    replaced = aliased(SheetRevision)
    rows = session.execute(
        select(SheetRevision, replaced.revision_label, Sheet.id)
        .join(Sheet, Sheet.id == SheetRevision.sheet_id)
        .outerjoin(replaced, replaced.superseded_by_id == SheetRevision.id)
        .where(SheetRevision.addendum_id == addendum.id)
        .order_by(SheetRevision.sheet_number)
    ).all()
    return [
        AffectedItem(
            kind="sheet",
            id=revision.id,
            reference=revision.sheet_number or "(unidentified sheet)",
            revision=revision.revision_label,
            state=revision.state,
            replaces=replaces,
            detail={"sheet_id": str(sheet_id), "title": revision.title},
        )
        for revision, replaces, sheet_id in rows
    ]


@provides_affected_items
def _documents(session: Session, addendum: Addendum) -> list[AffectedItem]:
    replaced = aliased(DocumentRevision)
    rows = session.execute(
        select(DocumentRevision, replaced.revision_label)
        .outerjoin(replaced, replaced.superseded_by_id == DocumentRevision.id)
        .where(DocumentRevision.addendum_id == addendum.id)
        .order_by(DocumentRevision.doc_key)
    ).all()
    return [
        AffectedItem(
            kind="document",
            id=revision.id,
            reference=revision.doc_key or "(unidentified document)",
            revision=revision.revision_label,
            state=revision.state,
            replaces=replaces,
            detail={"document_id": str(revision.document_id), "doc_type": revision.doc_type},
        )
        for revision, replaces in rows
    ]
