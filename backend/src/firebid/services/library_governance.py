"""Changes to the rate and productivity libraries go through a review queue (FR-LRN-03).

Anyone on a bid may propose a change: from a new quotation, from what a tender's outcome
showed, or as their own suggestion. A proposal is a record and nothing more: the library is
exactly as it was until an estimator with authority over that library approves it. Approval
applies the change as a new version of the entry, keeps the old version, and is audited. No
process changes a library by itself.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.audit import record_event
from firebid.db.models.commercial import RATE_SOURCES, Rate
from firebid.db.models.submission import (
    PROPOSAL_LIBRARIES,
    PROPOSAL_SOURCES,
    LibraryProposal,
)
from firebid.domain.actors import Actor, AuditContext
from firebid.domain.state_machines import Role
from firebid.domain.values import Money
from firebid.labour import productivity
from firebid.pricing.keys import ItemKey, unit_of

# Who may approve a change to each library: the roles the permission matrix gives it to.
APPROVERS = {"rate": str(Role.SENIOR_ESTIMATOR), "productivity": str(Role.SENIOR_ESTIMATOR)}


class ProposalError(ValueError):
    """A proposal that cannot be made or decided, with the reason a person can act on."""


def _decimal(value: Any, what: str) -> Decimal:
    try:
        found = Decimal(str(value))
    except (InvalidOperation, ValueError) as refusal:
        raise ProposalError(f"{what} is a number") from refusal
    if not found.is_finite() or found <= 0:
        raise ProposalError(f"{what} is more than zero")
    return found


def _day(value: Any, what: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as refusal:
        raise ProposalError(f"{what} is a date, as YYYY-MM-DD") from refusal


def _rate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    key = ItemKey.parse(str(payload.get("item_key") or "").strip().lower())
    if not key.type:
        raise ProposalError("a rate is for an item key, which starts with the type of item")
    unit = unit_of(str(payload.get("unit") or ""))
    if unit is None:
        raise ProposalError("a rate is quoted in a known unit")
    source_type = str(payload.get("source_type") or "")
    if source_type not in RATE_SOURCES:
        raise ProposalError("a rate's source is company_standard, purchase_order or quotation")
    reference = str(payload.get("source_reference") or "").strip()
    if not reference:
        raise ProposalError("a rate names its source: the standard, the order or the quotation")
    description = str(payload.get("description") or "").strip()
    if not description:
        raise ProposalError("a rate has a description")
    until = payload.get("valid_until")
    return {
        "item_key": key.text(),
        "unit": unit,
        "unit_rate": str(_decimal(payload.get("unit_rate"), "the rate").quantize(Decimal("0.01"))),
        "source_type": source_type,
        "source_reference": reference[:200],
        "description": description,
        "effective_from": _day(payload.get("effective_from"), "effective from").isoformat(),
        "valid_until": _day(until, "valid until").isoformat() if until else None,
    }


def _productivity_payload(payload: dict[str, Any]) -> dict[str, Any]:
    key = ItemKey.of(
        type=payload.get("item_type"), dn=payload.get("dn"), joining=payload.get("joining")
    )
    if not key.type:
        raise ProposalError("a productivity figure is for a type of item")
    unit = unit_of(str(payload.get("unit") or ""))
    if unit is None:
        raise ProposalError("a productivity figure is per a known unit")
    source_type = str(payload.get("source_type") or "")
    reference = str(payload.get("source_reference") or "").strip()
    try:
        productivity.check_source(source_type, reference)
    except productivity.Unsourced as refusal:
        raise ProposalError(str(refusal)) from refusal
    trade = str(payload.get("trade") or "").strip().lower().replace(" ", "_")
    description = str(payload.get("description") or "").strip()
    if not trade or not description:
        raise ProposalError("a productivity figure has a trade and a description")
    return {
        "item_type": key.type,
        "dn": key.dn,
        "joining": key.joining,
        "unit": unit,
        "hours_per_unit": str(_decimal(payload.get("hours_per_unit"), "man-hours per unit")),
        "trade": trade,
        "description": description,
        "source_type": source_type,
        "source_reference": reference[:200],
    }


def propose(
    session: Session,
    organisation_id: uuid.UUID,
    actor: Actor,
    *,
    library: str,
    payload: dict[str, Any],
    source: str,
    reason: str,
    source_ref: str | None = None,
) -> LibraryProposal:
    """Put a change in the queue. The library does not change."""
    if library not in PROPOSAL_LIBRARIES:
        raise ProposalError("a proposal is for the rate or the productivity library")
    if source not in PROPOSAL_SOURCES:
        raise ProposalError("a proposal comes from a quotation, an outcome or an estimator")
    if not reason.strip():
        raise ProposalError("say why the library should change")
    checked = _rate_payload(payload) if library == "rate" else _productivity_payload(payload)
    row = LibraryProposal(
        organisation_id=organisation_id,
        library=library,
        payload=checked,
        source=source,
        source_ref=(source_ref or "").strip()[:200] or None,
        reason=reason.strip(),
        state="proposed",
        proposed_by=actor.label,
        proposed_by_id=actor.id,
    )
    session.add(row)
    session.flush()
    record_event(
        session,
        context=AuditContext(organisation_id=organisation_id),
        actor=actor,
        action=f"{library} library: change proposed",
        entity_type=LibraryProposal.__tablename__,
        entity_id=row.id,
        after={"payload": checked, "source": source, "source_ref": row.source_ref},
        reason=row.reason,
    )
    return row


def proposals(
    session: Session, organisation_id: uuid.UUID, state: str | None = None
) -> list[LibraryProposal]:
    query = select(LibraryProposal).where(LibraryProposal.organisation_id == organisation_id)
    if state:
        query = query.where(LibraryProposal.state == state)
    return list(session.execute(query.order_by(LibraryProposal.created_at.desc())).scalars())


def _apply_rate(session: Session, row: LibraryProposal, actor: Actor) -> uuid.UUID:
    """The rate as a new version of its entry; the version before it is retired and kept."""
    values = row.payload
    old = session.execute(
        select(Rate).where(
            Rate.organisation_id == row.organisation_id,
            Rate.item_key == values["item_key"],
            Rate.unit == values["unit"],
            Rate.source_type == values["source_type"],
            Rate.source_reference == values["source_reference"],
            Rate.retired_at.is_(None),
        )
    ).scalar_one_or_none()
    if old is not None:
        old.retired_at = datetime.now(UTC)
        session.flush()  # the current-version index allows one current row
    rate = Rate(
        organisation_id=row.organisation_id,
        item_key=values["item_key"],
        key_parts=ItemKey.parse(values["item_key"]).parts(),
        description=values["description"],
        unit=values["unit"],
        unit_rate=Money.of(Decimal(values["unit_rate"])),
        currency="SGD",
        source_type=values["source_type"],
        source_reference=values["source_reference"],
        effective_from=date.fromisoformat(values["effective_from"]),
        valid_until=date.fromisoformat(values["valid_until"]) if values["valid_until"] else None,
        version=(old.version + 1) if old else 1,
        supersedes_id=old.id if old else None,
        created_by_id=actor.id,
    )
    session.add(rate)
    session.flush()
    return rate.id


def _apply_productivity(session: Session, row: LibraryProposal, actor: Actor) -> uuid.UUID:
    from firebid.services import labour

    values = row.payload
    try:
        entry = labour.set_entry(
            session,
            row.organisation_id,
            actor,
            item_type=values["item_type"],
            dn=values["dn"],
            joining=values["joining"],
            unit=values["unit"],
            hours=Decimal(values["hours_per_unit"]),
            trade=values["trade"],
            description=values["description"],
            source_type=values["source_type"],
            source_reference=values["source_reference"],
        )
    except labour.LabourError as refusal:
        raise ProposalError(str(refusal)) from refusal
    return entry.id


def decide(
    session: Session,
    row: LibraryProposal,
    actor: Actor,
    *,
    approve: bool,
    note: str | None = None,
) -> LibraryProposal:
    """An estimator approves the change, which applies it as a new version, or rejects it,
    which leaves the library as it was."""
    if row.state != "proposed":
        raise ProposalError(f"the proposal is already {row.state}")
    if actor.id is None:
        raise ProposalError("a library change is decided by a named person")
    if APPROVERS[row.library] not in actor.roles:
        raise ProposalError(
            f"a change to the {row.library} library is approved by the "
            f"{APPROVERS[row.library].replace('_', ' ')}"
        )
    if not approve and not (note or "").strip():
        raise ProposalError("say why the change is not made")
    if approve:
        row.applied_entry_id = (
            _apply_rate(session, row, actor)
            if row.library == "rate"
            else _apply_productivity(session, row, actor)
        )
    row.state = "approved" if approve else "rejected"
    row.decided_by, row.decided_by_id = actor.label, actor.id
    row.decided_at = datetime.now(UTC)
    row.note = note
    session.flush()
    record_event(
        session,
        context=AuditContext(organisation_id=row.organisation_id),
        actor=actor,
        action=f"{row.library} library: proposed change {row.state}",
        entity_type=LibraryProposal.__tablename__,
        entity_id=row.id,
        before={"state": "proposed"},
        after={
            "state": row.state,
            "applied_entry_id": str(row.applied_entry_id) if row.applied_entry_id else None,
            "payload": row.payload,
        },
        reason=note,
    )
    return row
