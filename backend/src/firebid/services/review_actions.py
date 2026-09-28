"""What a person does in the workbench (FR-REV-03, FR-REV-06).

* **Accept** verifies proposed items.
* **Edit** corrects an item's attributes, or its quantity (a count typed, a length
  re-measured), and verifies it as edited: an edit is a person's own answer.
* **Reject** rejects items.
* **Reject detections** says what the platform found on the drawing is not there: those
  detections leave takeoff, which is recomputed.
* **Undo** reverses one action, if nothing has changed the items since.

Every state change goes through the QTO item state machine, so each writes its audit event;
value changes write their own. Each action is kept as a `review_action` with every item's
state and values before and after, which is what undo reverses. Every edit and rejection is
also a labelled `correction_event` for the evaluation harness, with the detector and
calibration that made the proposal (derived labels only, requirements §11.4).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.audit import record_event
from firebid.db.models.core import Bid
from firebid.db.models.review import CorrectionEvent, ReviewAction
from firebid.db.models.takeoff import DetectedObject, PipeRun, QtoItem
from firebid.domain.actors import Actor, AuditContext
from firebid.domain.state_machines import QtoItemState
from firebid.domain.values import LengthMm
from firebid.services import qto, review
from firebid.services.transitions import apply_transition

ACCEPTABLE = (str(QtoItemState.PROPOSED), str(QtoItemState.EDITED))
REJECTABLE = (str(QtoItemState.PROPOSED), str(QtoItemState.EDITED), str(QtoItemState.VERIFIED))


class ReviewError(ValueError):
    """An action that cannot be taken, with the reason a person can act on."""


def _context(session: Session, bid_id: uuid.UUID) -> AuditContext:
    organisation = session.execute(select(Bid.organisation_id).where(Bid.id == bid_id))
    return AuditContext(organisation_id=organisation.scalar_one(), bid_id=bid_id)


def snapshot(item: QtoItem) -> dict[str, Any]:
    """An item's state and values, as an action records them before and after."""
    return {
        "state": item.state,
        "version": item.version,
        "description": item.description,
        "net_quantity": str(item.net_quantity),
        "length_mm": item.length.mm if item.length is not None else None,
        "allowance_percent": str(item.allowance_percent)
        if item.allowance_percent is not None
        else None,
        "attributes": dict(item.attributes or {}),
    }


def _check_reason(reason_code: str | None) -> str:
    codes = review.reason_codes()
    if not reason_code:
        raise ReviewError("give a reason for this change")
    if reason_code not in codes:
        raise ReviewError(f"unknown reason {reason_code!r}; use one of {', '.join(codes)}")
    return reason_code


def _items(session: Session, bid_id: uuid.UUID, item_ids: list[uuid.UUID]) -> list[QtoItem]:
    found = {
        item.id: item
        for item in session.execute(
            select(QtoItem).where(QtoItem.bid_id == bid_id, QtoItem.id.in_(item_ids))
        ).scalars()
    }
    missing = [str(i) for i in item_ids if i not in found]
    if missing:
        raise ReviewError(f"no such QTO item: {', '.join(missing)}")
    return [found[i] for i in item_ids]


def _record(
    session: Session,
    bid_id: uuid.UUID,
    kind: str,
    actor: Actor,
    entries: list[dict[str, Any]],
    reason_code: str | None = None,
    note: str | None = None,
    undoes: ReviewAction | None = None,
) -> ReviewAction:
    action = ReviewAction(
        bid_id=bid_id,
        kind=kind,
        actor_id=actor.id,
        actor_label=actor.label,
        reason_code=reason_code,
        note=note,
        entries=entries,
        undoes_id=undoes.id if undoes else None,
    )
    session.add(action)
    session.flush()
    return action


def _touched(entries: list[dict[str, Any]]) -> list[uuid.UUID]:
    return [uuid.UUID(str(e["item_id"])) for e in entries if "item_id" in e]


def _verify(session: Session, item: QtoItem, actor: Actor, note: str | None) -> None:
    apply_transition(session, item, target=QtoItemState.VERIFIED, actor=actor, reason=note)
    item.verified_by_id = actor.id
    item.verified_at = datetime.now(UTC)


def _reopen(session: Session, item: QtoItem, actor: Actor, reason: str) -> None:
    apply_transition(session, item, target=QtoItemState.PROPOSED, actor=actor, reason=reason)
    item.verified_by_id = None
    item.verified_at = None


# --- Accept ---------------------------------------------------------------------------------


def accept(
    session: Session,
    bid_id: uuid.UUID,
    item_ids: list[uuid.UUID],
    actor: Actor,
    note: str | None = None,
) -> ReviewAction:
    """Verify each item still waiting for a decision. Decided items are left as they are."""
    entries = []
    for item in _items(session, bid_id, item_ids):
        if item.state not in ACCEPTABLE:
            continue
        before = snapshot(item)
        _verify(session, item, actor, note)
        entries.append({"item_id": str(item.id), "before": before, "after": snapshot(item)})
    session.flush()
    action = _record(session, bid_id, "accept", actor, entries, note=note)
    qto.completeness(session, bid_id, only=_touched(entries))
    return action


# --- Reject ---------------------------------------------------------------------------------


def reject(
    session: Session,
    bid_id: uuid.UUID,
    item_ids: list[uuid.UUID],
    actor: Actor,
    reason_code: str | None,
    note: str | None = None,
) -> ReviewAction:
    code = _check_reason(reason_code)
    entries = []
    rejected = []
    for item in _items(session, bid_id, item_ids):
        if item.state not in REJECTABLE:
            continue
        before = snapshot(item)
        if item.state == str(QtoItemState.VERIFIED):
            _reopen(session, item, actor, f"reopened to reject: {code}")
        apply_transition(
            session, item, target=QtoItemState.REJECTED, actor=actor, reason=note or code
        )
        item.reason_code = code
        entries.append({"item_id": str(item.id), "before": before, "after": snapshot(item)})
        rejected.append((item, before))
    session.flush()
    action = _record(session, bid_id, "reject", actor, entries, code, note)
    for item, before in rejected:
        _correction(session, action, "reject", item, before, None, code, note, actor)
    qto.completeness(session, bid_id, only=_touched(entries))
    return action


# --- Edit -----------------------------------------------------------------------------------


def edit(
    session: Session,
    bid_id: uuid.UUID,
    item_id: uuid.UUID,
    actor: Actor,
    reason_code: str | None,
    note: str | None = None,
    *,
    attributes: dict[str, str] | None = None,
    quantity: Decimal | None = None,
    measured: qto.Measured | None = None,
    allowance_percent: Decimal | None = None,
) -> ReviewAction:
    """Correct an item and verify it as edited (two transitions, each audited)."""
    code = _check_reason(reason_code)
    [item] = _items(session, bid_id, [item_id])
    if item.state not in REJECTABLE:
        raise ReviewError(f"a {item.state} item cannot be edited")
    if attributes is None and quantity is None and measured is None and allowance_percent is None:
        raise ReviewError("nothing to change")
    is_length = item.unit == "m"
    if quantity is not None and is_length and not item.is_manual:
        raise ReviewError("re-measure a length on the drawing to change it")
    if measured is not None and not is_length:
        raise ReviewError("a count is corrected by its number, not measured")
    before = snapshot(item)
    if item.state == str(QtoItemState.VERIFIED):
        _reopen(session, item, actor, f"reopened to edit: {code}")
    apply_transition(session, item, target=QtoItemState.EDITED, actor=actor, reason=note or code)

    stated: dict[str, Any] = dict(item.attributes or {})
    for name, value in (attributes or {}).items():
        stated[name] = {"value": value, "source": f"edited by {actor.label}"}
    item.attributes = stated
    derivation: dict[str, Any] = dict(item.derivation or {})
    if quantity is not None:
        if quantity < 0:
            raise ReviewError("a quantity cannot be negative")
        item.net_quantity = quantity.quantize(Decimal("0.001"))
    if measured is not None:
        length_mm, measurement = qto._measure(session, bid_id, measured)
        item.length = LengthMm(length_mm)
        item.net_quantity = (Decimal(length_mm) / Decimal(1000)).quantize(Decimal("0.001"))
        derivation["remeasured"] = measurement
    if allowance_percent is not None:
        item.allowance_percent = allowance_percent
    derivation["edit"] = {
        "by": actor.label,
        "by_id": str(actor.id) if actor.id else None,
        "at": datetime.now(UTC).isoformat(),
        "reason_code": code,
        "note": note,
        "before": before,
    }
    item.derivation = derivation
    record_event(
        session,
        context=_context(session, bid_id),
        actor=actor,
        action="QTO item: edit values",
        entity_type=QtoItem.__tablename__,
        entity_id=item.id,
        before=before,
        after=snapshot(item),
        reason=note or code,
    )
    _verify(session, item, actor, "verified as edited")
    session.flush()
    action = _record(
        session,
        bid_id,
        "edit",
        actor,
        [{"item_id": str(item.id), "before": before, "after": snapshot(item)}],
        code,
        note,
    )
    _correction(session, action, "edit", item, before, snapshot(item), code, note, actor)
    qto.completeness(session, bid_id, only=[item.id])
    return action


# --- Detections that are not there ----------------------------------------------------------


def reject_detections(
    session: Session,
    bid_id: uuid.UUID,
    detection_ids: list[uuid.UUID],
    actor: Actor,
    reason_code: str | None,
    note: str | None = None,
) -> ReviewAction:
    """Nothing is installed where these were found: take them out of takeoff and recompute."""
    code = _check_reason(reason_code)
    entries: list[dict[str, Any]] = []
    corrections = []
    for row in _detections(session, bid_id, detection_ids):
        if row.state == "rejected":
            continue
        entries.append(
            {
                "detection_id": str(row.id),
                "table": row.__tablename__,
                "before": {"state": row.state},
                "after": {"state": "rejected"},
            }
        )
        corrections.append(row)
        record_event(
            session,
            context=_context(session, bid_id),
            actor=actor,
            action="detection: reject as not there",
            entity_type=row.__tablename__,
            entity_id=row.id,
            before={"state": row.state},
            after={"state": "rejected"},
            reason=note or code,
        )
        row.state = "rejected"
    if not entries:
        raise ReviewError("none of these detections can be rejected")
    session.flush()
    action = _record(session, bid_id, "reject_detections", actor, entries, code, note)
    for row in corrections:
        _detection_correction(session, action, row, code, note, actor)
    qto.recompute(session, bid_id)
    return action


def _detections(
    session: Session, bid_id: uuid.UUID, ids: list[uuid.UUID]
) -> list[DetectedObject | PipeRun]:
    found: list[DetectedObject | PipeRun] = list(
        session.execute(
            select(DetectedObject).where(
                DetectedObject.bid_id == bid_id, DetectedObject.id.in_(ids)
            )
        ).scalars()
    )
    found.extend(
        session.execute(
            select(PipeRun).where(PipeRun.bid_id == bid_id, PipeRun.id.in_(ids))
        ).scalars()
    )
    return found


# --- Undo -----------------------------------------------------------------------------------


def undo(session: Session, bid_id: uuid.UUID, action_id: uuid.UUID, actor: Actor) -> ReviewAction:
    """Reverse one action, if every item it touched is still as the action left it."""
    action = session.get(ReviewAction, action_id)
    if action is None or action.bid_id != bid_id:
        raise ReviewError("no such action")
    if action.kind == "undo":
        raise ReviewError("an undo is not undone; take the action again")
    if action.undone_at is not None:
        raise ReviewError("this action has already been undone")
    entries: list[dict[str, Any]] = []
    if action.kind == "reject_detections":
        rows = {
            str(row.id): row
            for row in _detections(
                session, bid_id, [uuid.UUID(str(e["detection_id"])) for e in action.entries]
            )
        }
        for entry in action.entries:
            row = rows.get(str(entry["detection_id"]))
            if row is None or row.state != "rejected":
                raise ReviewError("a detection has changed since; this cannot be undone")
            was: dict[str, Any] = cast("dict[str, Any]", entry["before"])
            row.state = str(was["state"])
            entries.append({**entry, "before": entry["after"], "after": was})
        session.flush()
        qto.recompute(session, bid_id)
    else:
        items = _items(session, bid_id, [uuid.UUID(str(e["item_id"])) for e in action.entries])
        for item, entry in zip(items, action.entries, strict=True):
            after = cast("dict[str, Any]", entry["after"])
            if item.state != after["state"] or item.version != after["version"]:
                raise ReviewError(f"{item.human_id} has changed since; this cannot be undone")
        for item, entry in zip(items, action.entries, strict=True):
            before = cast("dict[str, Any]", entry["before"])
            now = snapshot(item)
            _restore(session, item, before, actor)
            entries.append({"item_id": str(item.id), "before": now, "after": snapshot(item)})
        qto.completeness(session, bid_id, only=_touched(entries))
    action.undone_at = datetime.now(UTC)
    session.flush()
    return _record(session, bid_id, "undo", actor, entries, undoes=action)


def _restore(session: Session, item: QtoItem, before: dict[str, Any], actor: Actor) -> None:
    """Put an item back as it was, through the state machine."""
    reason = "undo"
    if item.state == str(QtoItemState.VERIFIED):
        _reopen(session, item, actor, reason)
    elif item.state == str(QtoItemState.REJECTED):
        apply_transition(session, item, target=QtoItemState.PROPOSED, actor=actor, reason=reason)
        item.reason_code = None
    derivation: dict[str, Any] = dict(item.derivation or {})
    if derivation.pop("edit", None) is not None:
        item.attributes = dict(before["attributes"])
        item.net_quantity = Decimal(before["net_quantity"])
        item.length = LengthMm(before["length_mm"]) if before["length_mm"] is not None else None
        item.allowance_percent = (
            Decimal(before["allowance_percent"]) if before["allowance_percent"] else None
        )
        derivation.pop("remeasured", None)
        item.derivation = derivation
        record_event(
            session,
            context=_context(session, item.bid_id),
            actor=actor,
            action="QTO item: edit undone",
            entity_type=QtoItem.__tablename__,
            entity_id=item.id,
            after=snapshot(item),
            reason=reason,
        )
    if before["state"] in (str(QtoItemState.VERIFIED), str(QtoItemState.EDITED)):
        _verify(session, item, actor, reason)


# --- Correction events (FR-REV-06) ----------------------------------------------------------


def _provenance(session: Session, item: QtoItem) -> dict[str, Any]:
    """Which detector and calibration made the proposal: from its first member detection."""
    derivation: dict[str, Any] = dict(item.derivation or {})
    ids = [m.get("id") for m in derivation.get("members") or [] if isinstance(m, dict)]
    keys = []
    for member in ids:
        try:
            keys.append(uuid.UUID(str(member)))
        except ValueError:
            continue
    rows = _detections(session, item.bid_id, keys[:1]) if keys else []
    found = rows[0] if rows else None
    rule = derivation.get("rule") or {}
    return {
        "detector_method": derivation.get("detection_method"),
        "detector_version": getattr(found, "detector_version", None),
        "calibration_version": getattr(found, "calibration_version", None),
        "rule_version": derivation.get("rule_set_version")
        if not rule
        else f"{rule.get('rule_key')}@{rule.get('rule_version')}",
    }


def _consultant(session: Session, bid_id: uuid.UUID) -> str | None:
    from firebid.services import symbols

    try:
        return symbols.consultant_of(session, bid_id).key
    except Exception:
        return None


def _correction(
    session: Session,
    action: ReviewAction,
    kind: str,
    item: QtoItem,
    before: dict[str, Any],
    after: dict[str, Any] | None,
    code: str,
    note: str | None,
    actor: Actor,
) -> None:
    session.add(
        CorrectionEvent(
            bid_id=item.bid_id,
            kind=kind,
            action_id=action.id,
            qto_item_id=item.id,
            object_type=item.item_type,
            before={k: v for k, v in before.items() if k != "description"},
            after={k: v for k, v in after.items() if k != "description"} if after else None,
            reason_code=code,
            note=note,
            confidence=item.confidence,
            consultant_key=_consultant(session, item.bid_id),
            actor_id=actor.id,
            data_policy="derived-labels-only",
            **_provenance(session, item),
        )
    )
    session.flush()


def _detection_correction(
    session: Session,
    action: ReviewAction,
    row: DetectedObject | PipeRun,
    code: str,
    note: str | None,
    actor: Actor,
) -> None:
    object_type = row.object_type if isinstance(row, DetectedObject) else f"pipe_{row.run_class}"
    method = row.extraction_method if isinstance(row, DetectedObject) else "network"
    session.add(
        CorrectionEvent(
            bid_id=row.bid_id,
            kind="false_detection",
            action_id=action.id,
            detection_id=row.id,
            object_type=object_type,
            before={"state": "proposed", "object_type": object_type},
            after=None,
            reason_code=code,
            note=note,
            detector_method=method,
            detector_version=row.detector_version,
            calibration_version=row.calibration_version,
            confidence=row.confidence,
            consultant_key=_consultant(session, row.bid_id),
            actor_id=actor.id,
            data_policy="derived-labels-only",
        )
    )
    session.flush()


def correction_rows(session: Session, bid_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    """The labelled corrections, as the evaluation harness reads them."""
    query = select(CorrectionEvent).order_by(CorrectionEvent.created_at)
    if bid_id is not None:
        query = query.where(CorrectionEvent.bid_id == bid_id)
    return [
        {
            "id": str(row.id),
            "bid_id": str(row.bid_id),
            "kind": row.kind,
            "object_type": row.object_type,
            "before": row.before,
            "after": row.after,
            "reason_code": row.reason_code,
            "detector_method": row.detector_method,
            "detector_version": row.detector_version,
            "calibration_version": row.calibration_version,
            "rule_version": row.rule_version,
            "confidence": row.confidence,
            "consultant_key": row.consultant_key,
            "data_policy": row.data_policy,
            "created_at": row.created_at.isoformat(),
        }
        for row in session.execute(query).scalars()
    ]
