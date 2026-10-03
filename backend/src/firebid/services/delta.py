"""Baselines of a bid's takeoff, and what changed since one (FR-QTO-12).

* **Snapshots.** The takeoff as it stood is recorded when G1 is approved and when an
  addendum is registered: every live item with its quantity, state and the BOQ line it was
  in. A snapshot is never changed.
* **The delta report** is the takeoff now against a snapshot (`qto.delta`): items added,
  removed and changed with their value before, whose verification was kept, and the
  quantity change per BOQ line.
* **Reopening.** When a recompute changes what G1 approved, that approval is marked
  reopened, with the items that changed: it no longer counts as passed, and those items are
  what a person verifies before the gate is approved again. Everything else keeps its
  verification.
* **Addenda.** What an addendum changed in the takeoff, the bill and the clarification
  candidates is the delta from the snapshot taken when it was registered to the one taken
  for the next addendum, or to now. Registered as providers of `addenda.affected_items`.

Re-processing only the affected sheets is not done here: it is how the pipeline already
works. A new revision is a new sheet and is read once; every other sheet's detection is
found unchanged by its fingerprint; and a recompute leaves alone every item whose inputs
are what they were (P1-07).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.audit import record_event
from firebid.db.models.baseline import TakeoffSnapshot
from firebid.db.models.commercial import BoqLine, BoqLineSource, ClientBoqLine, ClientBoqMapping
from firebid.db.models.core import Bid
from firebid.db.models.documents import Addendum
from firebid.db.models.takeoff import QtoItem
from firebid.db.models.workflow import Approval
from firebid.domain.actors import SYSTEM_ACTOR, AuditContext
from firebid.qto import delta
from firebid.qto.delta import Entry, Report
from firebid.services import addenda, qto
from firebid.services.addenda import AffectedItem

log = structlog.get_logger("firebid.delta")


class DeltaError(ValueError):
    """A report that cannot be made, with the reason a person can act on."""


def _lines(session: Session, bid_id: uuid.UUID) -> dict[uuid.UUID, BoqLine]:
    """The line of the current company BOQ each item is in."""
    from firebid.services import boq

    current = boq.current_boq(session, bid_id)
    if current is None:
        return {}
    return {
        item_id: line
        for item_id, line in session.execute(
            select(BoqLineSource.qto_item_id, BoqLine)
            .join(BoqLine, BoqLine.id == BoqLineSource.boq_line_id)
            .where(BoqLine.boq_id == current.id)
        )
    }


def _line_name(line: BoqLine | None) -> str | None:
    if line is None:
        return None
    return f"{line.item_no} {line.description}".strip() if line.item_no else line.description


def entries(session: Session, bid_id: uuid.UUID, items: list[QtoItem] | None = None) -> list[Entry]:
    """The takeoff as it stands, as a snapshot holds it."""
    lines = _lines(session, bid_id)
    return [
        Entry(
            human_id=item.human_id,
            version=item.version,
            description=item.description,
            unit=item.unit,
            quantity=item.net_quantity,
            state=item.state,
            inputs_hash=item.inputs_hash,
            line=_line_name(lines.get(item.id)),
            classification=item.classification,
            level=item.level,
        )
        for item in (qto.live_items(session, bid_id) if items is None else items)
    ]


def take_snapshot(
    session: Session,
    bid_id: uuid.UUID,
    reason: str,
    *,
    addendum_id: uuid.UUID | None = None,
    approval_id: uuid.UUID | None = None,
) -> TakeoffSnapshot:
    items = qto.live_items(session, bid_id)
    row = TakeoffSnapshot(
        bid_id=bid_id,
        reason=reason,
        addendum_id=addendum_id,
        approval_id=approval_id,
        snapshot_hash=qto.snapshot_hash(items),
        items=[entry.as_json() for entry in entries(session, bid_id, items)],
    )
    session.add(row)
    session.flush()
    log.info("takeoff_snapshot", bid_id=str(bid_id), reason=reason, items=len(items))
    return row


def snapshots(session: Session, bid_id: uuid.UUID) -> list[TakeoffSnapshot]:
    """The bid's snapshots, newest first."""
    return list(
        session.execute(
            select(TakeoffSnapshot)
            .where(TakeoffSnapshot.bid_id == bid_id)
            .order_by(TakeoffSnapshot.created_at.desc(), TakeoffSnapshot.id)
        ).scalars()
    )


def _entries_of(snapshot: TakeoffSnapshot) -> list[Entry]:
    return [Entry.from_json(dict(item)) for item in snapshot.items]


def report(
    session: Session, bid_id: uuid.UUID, snapshot_id: uuid.UUID | None = None
) -> tuple[TakeoffSnapshot, Report]:
    """The takeoff now against a baseline: the snapshot named, or the latest one."""
    if snapshot_id is None:
        found = snapshots(session, bid_id)
        if not found:
            raise DeltaError(
                "this bid has no baseline yet: one is taken when G1 is approved and when an "
                "addendum is registered"
            )
        baseline = found[0]
    else:
        row = session.get(TakeoffSnapshot, snapshot_id)
        if row is None or row.bid_id != bid_id:
            raise DeltaError("no such baseline on this bid")
        baseline = row
    return baseline, delta.report(_entries_of(baseline), entries(session, bid_id))


# --- Gates (reopened for the items that changed, and only those) ----------------------------


def approved_g1(session: Session, bid_id: uuid.UUID) -> Approval | None:
    """The G1 approval in force: the latest one, unless a change has reopened it."""
    latest = (
        session.execute(
            select(Approval)
            .where(Approval.bid_id == bid_id, Approval.gate == "G1")
            .order_by(Approval.decided_at.desc())
        )
        .scalars()
        .first()
    )
    if latest is None or latest.decision != "approved" or latest.reopened_at is not None:
        return None
    return latest


def reopen_if_changed(session: Session, bid_id: uuid.UUID, items: list[QtoItem]) -> list[str]:
    """After a recompute: if the takeoff is no longer what G1 approved, reopen G1 for the
    items that changed. Returns their human IDs; empty when the approval still holds."""
    approval = approved_g1(session, bid_id)
    if approval is None or approval.snapshot_hash == qto.snapshot_hash(items):
        return []
    baseline = session.execute(
        select(TakeoffSnapshot).where(TakeoffSnapshot.approval_id == approval.id)
    ).scalar_one_or_none()
    changed = (
        [
            item.human_id
            for item in delta.report(_entries_of(baseline), entries(session, bid_id, items)).items
            if item.change != "unchanged" or item.state_before != item.state_after
        ]
        if baseline is not None
        else sorted(item.human_id for item in items if item.state not in delta.VERIFIED)
    )
    approval.reopened_at = datetime.now(UTC)
    approval.reopened_reason = (
        f"the takeoff changed after approval: {len(changed)} item(s) to verify again"
    )
    approval.reopened_items = changed
    organisation_id = session.execute(
        select(Bid.organisation_id).where(Bid.id == bid_id)
    ).scalar_one()
    record_event(
        session,
        context=AuditContext(organisation_id=organisation_id, bid_id=bid_id),
        actor=SYSTEM_ACTOR,
        action="gate G1: reopened",
        entity_type=Approval.__tablename__,
        entity_id=approval.id,
        before={"gate": "G1", "decision": "approved", "snapshot_hash": approval.snapshot_hash},
        after={"reopened_items": changed},
        reason=approval.reopened_reason,
    )
    session.flush()
    log.info("gate_reopened", bid_id=str(bid_id), gate="G1", items=len(changed))
    return changed


def gate_status(session: Session, bid_id: uuid.UUID) -> dict[str, Any]:
    """Where G1 stands: never approved, approved, or reopened and for which items."""
    latest = (
        session.execute(
            select(Approval)
            .where(Approval.bid_id == bid_id, Approval.gate == "G1")
            .order_by(Approval.decided_at.desc())
        )
        .scalars()
        .first()
    )
    if latest is None or latest.decision != "approved":
        return {"gate": "G1", "status": "open", "reopened_items": []}
    if latest.reopened_at is None:
        return {
            "gate": "G1",
            "status": "approved",
            "approved_at": latest.decided_at,
            "reopened_items": [],
        }
    live = {item.human_id: item for item in qto.live_items(session, bid_id)}
    waiting = [
        human_id
        for human_id in latest.reopened_items or []
        if human_id in live and live[human_id].state not in (*delta.VERIFIED, "rejected")
    ]
    return {
        "gate": "G1",
        "status": "reopened",
        "approved_at": latest.decided_at,
        "reopened_at": latest.reopened_at,
        "reason": latest.reopened_reason,
        "reopened_items": list(latest.reopened_items or []),
        "to_verify": waiting,
    }


# --- What an addendum changed (the P1-02 query, extended) -----------------------------------


def addendum_report(session: Session, addendum: Addendum) -> Report | None:
    """The takeoff's delta from when the addendum was registered to when the next one was,
    or to now. None when no takeoff existed for it to change."""
    taken = list(
        session.execute(
            select(TakeoffSnapshot)
            .where(
                TakeoffSnapshot.bid_id == addendum.bid_id,
                TakeoffSnapshot.addendum_id.is_not(None),
            )
            .order_by(TakeoffSnapshot.created_at, TakeoffSnapshot.id)
        ).scalars()
    )
    own = next((row for row in taken if row.addendum_id == addendum.id), None)
    if own is None:
        return None
    later = taken[taken.index(own) + 1 :]
    after = _entries_of(later[0]) if later else entries(session, addendum.bid_id)
    return delta.report(_entries_of(own), after)


def _latest_rows(session: Session, bid_id: uuid.UUID, human_ids: set[str]) -> dict[str, QtoItem]:
    rows: dict[str, QtoItem] = {}
    if not human_ids:
        return rows
    for item in session.execute(
        select(QtoItem)
        .where(QtoItem.bid_id == bid_id, QtoItem.human_id.in_(sorted(human_ids)))
        .order_by(QtoItem.human_id, QtoItem.version)
    ).scalars():
        rows[item.human_id] = item  # the last of each is its newest version
    return rows


@addenda.provides_affected_items
def _qto_items(session: Session, addendum: Addendum) -> list[AffectedItem]:
    found = addendum_report(session, addendum)
    if found is None:
        return []
    changed = found.changed()
    rows = _latest_rows(session, addendum.bid_id, {item.human_id for item in changed})
    return [
        AffectedItem(
            kind="qto_item",
            id=rows[item.human_id].id,
            reference=item.human_id,
            revision=f"v{rows[item.human_id].version}",
            state=item.state_after or "superseded",
            replaces=str(item.before) if item.before is not None else None,
            detail=item.as_json(),
        )
        for item in changed
        if item.human_id in rows
    ]


@addenda.provides_affected_items
def _boq_lines(session: Session, addendum: Addendum) -> list[AffectedItem]:
    found = addendum_report(session, addendum)
    if found is None:
        return []
    by_name = {_line_name(line): line for line in _lines(session, addendum.bid_id).values()}
    out = []
    for line in found.lines:
        row = by_name.get(line.line)
        if row is None:
            continue  # items in no line of the bill: they are listed as QTO items
        out.append(
            AffectedItem(
                kind="boq_line",
                id=row.id,
                reference=line.line,
                revision=None,
                state="quantity changed" if line.difference else "items changed",
                replaces=str(line.before),
                detail=line.as_json(),
            )
        )
    return out


@addenda.provides_affected_items
def _clarification_candidates(session: Session, addendum: Addendum) -> list[AffectedItem]:
    """Client BOQ lines flagged for a clarification (FR-BOQ-03) whose measured quantity
    rests on an item the addendum changed: the variance is to be looked at again."""
    found = addendum_report(session, addendum)
    if found is None:
        return []
    changed = {item.human_id for item in found.changed()}
    if not changed:
        return []
    lines = _lines(session, addendum.bid_id)
    # The bill was built from the item as it was: any version of a changed item counts.
    versions = session.execute(
        select(QtoItem.id).where(
            QtoItem.bid_id == addendum.bid_id, QtoItem.human_id.in_(sorted(changed))
        )
    ).scalars()
    touched = {lines[item_id].id for item_id in versions if item_id in lines}
    if not touched:
        return []
    return [
        AffectedItem(
            kind="clarification_candidate",
            id=mapping.id,
            reference=f"{client.item_no or ''} {client.description or ''}".strip(),
            revision=None,
            state=mapping.state,
            detail={
                "boq_line_id": str(mapping.boq_line_id),
                "client_quantity": str(client.quantity) if client.quantity is not None else None,
                "measured_quantity": str(mapping.measured_quantity)
                if mapping.measured_quantity is not None
                else None,
                "variance_percent": mapping.variance_percent,
            },
        )
        for mapping, client in session.execute(
            select(ClientBoqMapping, ClientBoqLine)
            .join(ClientBoqLine, ClientBoqLine.id == ClientBoqMapping.client_boq_line_id)
            .where(
                ClientBoqMapping.bid_id == addendum.bid_id,
                ClientBoqMapping.flagged.is_(True),
                ClientBoqMapping.boq_line_id.in_(sorted(touched)),
            )
            .order_by(ClientBoqLine.row_index)
        )
    ]
