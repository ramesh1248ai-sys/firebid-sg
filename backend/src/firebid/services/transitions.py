"""Applying a state transition: check the rules, move the state, record exactly one event.

Nothing else writes a `state` column. Guard context is read from the database here, so callers
cannot skip a rule by forgetting to pass it.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from firebid.db.audit import record_event
from firebid.db.models.core import Bid
from firebid.db.models.documents import SheetRevision
from firebid.db.models.takeoff import QtoItem
from firebid.db.models.workflow import Approval
from firebid.domain.actors import Actor, AuditContext
from firebid.domain.state_machines import (
    BID_LIFECYCLE,
    QTO_ITEM,
    SHEET_REVISION,
    BidState,
    QtoItemState,
    SheetRevisionState,
    StateMachine,
    Transition,
    plan_transition,
)


class Stateful(Protocol):
    id: uuid.UUID
    state: str


_MACHINES: dict[type, StateMachine] = {
    Bid: BID_LIFECYCLE,
    SheetRevision: SHEET_REVISION,
    QtoItem: QTO_ITEM,
}


def machine_for(entity: object) -> StateMachine:
    machine = _MACHINES.get(type(entity))
    if machine is None:
        raise TypeError(f"{type(entity).__name__} has no state machine")
    return machine


def _audit_context(session: Session, entity: Stateful) -> AuditContext:
    if isinstance(entity, Bid):
        return AuditContext(organisation_id=entity.organisation_id, bid_id=entity.id)
    bid_id = getattr(entity, "bid_id", None)
    if bid_id is None:
        raise TypeError(f"{type(entity).__name__} is not bid-scoped")
    organisation_id = session.execute(
        select(Bid.organisation_id).where(Bid.id == bid_id)
    ).scalar_one()
    return AuditContext(organisation_id=organisation_id, bid_id=bid_id)


def _guard_context(session: Session, entity: Stateful, target: StrEnum) -> dict[str, Any]:
    """Facts the guards need, read from the database rather than trusted from the caller."""
    if isinstance(entity, Bid) and target == BidState.SUBMITTED:
        approved = session.execute(
            select(func.count())
            .select_from(Approval)
            .where(
                Approval.bid_id == entity.id,
                Approval.gate == "G4",
                Approval.decision == "approved",
            )
        ).scalar_one()
        return {"has_g4_approval": bool(approved)}

    if isinstance(entity, SheetRevision) and target == SheetRevisionState.CURRENT:
        other = session.execute(
            select(SheetRevision.revision_label).where(
                SheetRevision.bid_id == entity.bid_id,
                SheetRevision.sheet_number == entity.sheet_number,
                SheetRevision.state == str(SheetRevisionState.CURRENT),
                SheetRevision.id != entity.id,
            )
        ).scalar_one_or_none()
        return {"other_current_revision": other}

    if isinstance(entity, QtoItem) and target == QtoItemState.BASELINED:
        unresolved = entity.duplicate_group_id is not None
        return {"unresolved_duplicates": unresolved}

    return {}


def apply_transition(
    session: Session,
    entity: Stateful,
    *,
    target: StrEnum,
    actor: Actor,
    reason: str | None = None,
    extra_context: dict[str, Any] | None = None,
) -> Transition:
    """Move ``entity`` to ``target``, or raise ``TransitionError``. Writes one audit event."""
    machine = machine_for(entity)
    source = machine.states(entity.state)
    context = _guard_context(session, entity, target) | (extra_context or {})

    transition = plan_transition(
        machine, source=source, target=target, actor_roles=actor.roles, context=context
    )

    entity.state = str(target)
    record_event(
        session,
        context=_audit_context(session, entity),
        actor=actor,
        action=f"{machine.name}: {transition.action}",
        entity_type=type(entity).__tablename__,  # type: ignore[attr-defined]
        entity_id=entity.id,
        before={"state": str(source)},
        after={"state": str(target)},
        reason=reason,
    )
    return transition
