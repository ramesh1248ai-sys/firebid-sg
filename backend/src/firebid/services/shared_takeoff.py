"""One verified takeoff for a project's several bids (FR-BID-04; ADR-012).

A project bid to several main contractors is taken off once. The bid that did the takeoff
**publishes** its verified items to the project, once G1 is approved; each of the project's
other bids **adopts** the published takeoff.

* What is published is quantities and where they came from by drawing number and revision:
  nothing of the publishing bid's client documents, client BOQ, commercial terms or prices.
  Those stay bid-scoped, in the query layer and under row-level security.
* An adopted item is a row of the adopting bid that refers to the published item and its
  version. It arrives verified by the person who adopted it, and says who verified it in
  the publishing bid. Recomputing the adopting bid's own takeoff leaves it alone.
* Copy-on-write: an edit in the adopting bid changes that bid's row only. When a later
  version is published and adopted, rows nobody edited follow it; an edited row is kept as
  it is and reported, for a person to reconcile.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from firebid.db.audit import record_event
from firebid.db.ids import next_qto_id
from firebid.db.models.baseline import SharedTakeoff
from firebid.db.models.core import AppUser, Bid
from firebid.db.models.takeoff import QtoItem
from firebid.domain.actors import SYSTEM_ACTOR, Actor, AuditContext
from firebid.domain.state_machines import QtoItemState
from firebid.domain.values import LengthMm
from firebid.services import delta, qto
from firebid.services.transitions import apply_transition

log = structlog.get_logger("firebid.shared_takeoff")

DONE = ("verified", "baselined")


class SharedTakeoffError(ValueError):
    """A publish or adopt that cannot be done, with the reason a person can act on."""


def _published(item: QtoItem, verifier: str | None) -> dict[str, Any]:
    derivation: dict[str, Any] = dict(item.derivation or {})
    return {
        "human_id": item.human_id,
        "version": item.version,
        "item_type": item.item_type,
        "classification": item.classification,
        "description": item.description,
        "attributes": dict(item.attributes or {}),
        "unit": item.unit,
        "net_quantity": str(item.net_quantity),
        "allowance_percent": str(item.allowance_percent)
        if item.allowance_percent is not None
        else None,
        "length_mm": item.length.mm if item.length is not None else None,
        "level": item.level,
        "zone": item.zone,
        "grid_from": item.grid_from,
        "grid_to": item.grid_to,
        "calculation_method": item.calculation_method,
        "rule_key": item.rule_key,
        "rule_version": item.rule_version,
        "is_manual": item.is_manual,
        "confidence": item.confidence,
        "verified_by": verifier,
        "verified_at": item.verified_at.isoformat() if item.verified_at else None,
        "inputs_hash": item.inputs_hash,
        # How it was reached, by drawing number and revision. Row identifiers of the
        # publishing bid's sheets and detections mean nothing in another bid.
        "derivation": {
            "members": derivation.get("members") or [],
            "sources": derivation.get("sources") or [],
            "geometry": derivation.get("geometry") or [],
            "rule": derivation.get("rule"),
            "note": derivation.get("note"),
            "detection_method": derivation.get("detection_method"),
            "rule_set_version": derivation.get("rule_set_version"),
        },
    }


def _digest(item: dict[str, Any]) -> str:
    """What an adopted row is compared by: the published item's content."""
    material = {k: v for k, v in item.items() if k not in ("verified_by", "verified_at")}
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()


def publish(session: Session, bid: Bid, actor: Actor, note: str | None = None) -> SharedTakeoff:
    """Publish the bid's verified takeoff to its project. G1 must be approved and in force."""
    if delta.approved_g1(session, bid.id) is None:
        raise SharedTakeoffError(
            "a takeoff is published to its project once G1 is approved, and while it holds"
        )
    items = [
        i for i in qto.live_items(session, bid.id) if i.state in DONE and not qto.is_adopted(i)
    ]
    if not items:
        raise SharedTakeoffError("this bid has no verified takeoff of its own to publish")
    names = {
        user.id: user.display_name
        for user in session.execute(
            select(AppUser).where(AppUser.id.in_({i.verified_by_id for i in items} - {None}))
        ).scalars()
    }
    version = (
        session.execute(
            select(func.coalesce(func.max(SharedTakeoff.version), 0)).where(
                SharedTakeoff.project_id == bid.project_id
            )
        ).scalar_one()
        + 1
    )
    row = SharedTakeoff(
        organisation_id=bid.organisation_id,
        project_id=bid.project_id,
        source_bid_id=bid.id,
        source_bid_human_id=bid.human_id,
        version=version,
        snapshot_hash=qto.snapshot_hash(items),
        items=[
            _published(i, names.get(i.verified_by_id) if i.verified_by_id else None) for i in items
        ],
        published_by_id=actor.id,
        published_by=actor.label,
        note=note,
    )
    session.add(row)
    session.flush()
    record_event(
        session,
        context=AuditContext(organisation_id=bid.organisation_id, bid_id=bid.id),
        actor=actor,
        action="shared takeoff: published",
        entity_type=SharedTakeoff.__tablename__,
        entity_id=row.id,
        after={"version": version, "items": len(items), "snapshot_hash": row.snapshot_hash},
        reason=note,
    )
    log.info("shared_takeoff_published", project_id=str(bid.project_id), version=version)
    return row


def published(session: Session, project_id: uuid.UUID) -> list[SharedTakeoff]:
    """The project's published takeoffs, newest first."""
    return list(
        session.execute(
            select(SharedTakeoff)
            .where(SharedTakeoff.project_id == project_id)
            .order_by(SharedTakeoff.version.desc())
        ).scalars()
    )


@dataclass
class Adoption:
    shared: SharedTakeoff
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    # Rows this bid edited, whose published item has since changed or gone: left as they are.
    kept: list[dict[str, Any]] = field(default_factory=list)
    withdrawn: list[str] = field(default_factory=list)


def _diverged(item: QtoItem) -> bool:
    """Whether this bid has made the adopted row its own: edited it, or rejected it."""
    return bool(dict(item.derivation or {}).get("edit")) or item.state not in DONE


def _create(
    session: Session,
    bid: Bid,
    shared: SharedTakeoff,
    source: dict[str, Any],
    actor: Actor,
    previous: QtoItem | None,
) -> QtoItem:
    derivation = dict(source.get("derivation") or {})
    derivation["shared"] = {
        "shared_takeoff_id": str(shared.id),
        "version": shared.version,
        "source_bid": shared.source_bid_human_id,
        "source_human_id": source["human_id"],
        "digest": _digest(source),
        "verified_by": source.get("verified_by"),
        "verified_at": source.get("verified_at"),
    }
    item = QtoItem(
        bid_id=bid.id,
        human_id=previous.human_id if previous else next_qto_id(session, bid.id),
        item_type=source["item_type"],
        classification=source.get("classification"),
        description=source["description"],
        attributes=source.get("attributes") or {},
        unit=source["unit"],
        net_quantity=Decimal(str(source["net_quantity"])),
        allowance_percent=Decimal(str(source["allowance_percent"]))
        if source.get("allowance_percent") is not None
        else None,
        length=LengthMm(int(source["length_mm"])) if source.get("length_mm") is not None else None,
        level=source.get("level"),
        zone=source.get("zone"),
        grid_from=source.get("grid_from"),
        grid_to=source.get("grid_to"),
        calculation_method=source["calculation_method"],
        rule_key=source.get("rule_key"),
        rule_version=source.get("rule_version"),
        is_manual=False,
        confidence=source.get("confidence"),
        state=str(QtoItemState.DETECTED),
        version=previous.version + 1 if previous else 1,
        supersedes_id=previous.id if previous else None,
        item_key=f"shared:{shared.source_bid_human_id}:{source['human_id']}",
        inputs_hash=source.get("inputs_hash"),
        derivation=derivation,
    )
    session.add(item)
    session.flush()
    apply_transition(
        session,
        item,
        target=QtoItemState.PROPOSED,
        actor=SYSTEM_ACTOR,
        reason=f"from the project's shared takeoff v{shared.version}",
    )
    qto.verify(
        session,
        item,
        actor,
        f"adopted from {shared.source_bid_human_id} {source['human_id']} "
        f"(shared takeoff v{shared.version}), verified there by "
        f"{source.get('verified_by') or 'a person'}",
    )
    return item


def adopt(session: Session, bid: Bid, actor: Actor, shared_id: uuid.UUID | None = None) -> Adoption:
    """Adopt the project's published takeoff into this bid: the one named, or the latest."""
    available = published(session, bid.project_id)
    shared = (
        next((row for row in available if row.id == shared_id), None)
        if shared_id
        else (available[0] if available else None)
    )
    if shared is None:
        raise SharedTakeoffError("this project has no published takeoff to adopt")
    if shared.source_bid_id == bid.id:
        raise SharedTakeoffError("this bid published that takeoff: it is already its own")
    own = [i for i in qto.live_items(session, bid.id) if not qto.is_adopted(i)]
    if own:
        raise SharedTakeoffError(
            f"this bid has {len(own)} takeoff item(s) of its own: a bid either takes off its "
            "own drawings or adopts the project's takeoff, not both"
        )
    adopted = {
        str(dict(item.derivation)["shared"]["source_human_id"]): item  # type: ignore[index]
        for item in qto.live_items(session, bid.id)
        if qto.is_adopted(item)
    }
    outcome = Adoption(shared)
    seen = set()
    for source in shared.items:
        source = dict(source)
        human_id = str(source["human_id"])
        seen.add(human_id)
        current = adopted.get(human_id)
        if current is None:
            outcome.created.append(_create(session, bid, shared, source, actor, None).human_id)
            continue
        reference: dict[str, Any] = dict(current.derivation)["shared"]  # type: ignore[assignment]
        if reference.get("digest") == _digest(source):
            outcome.unchanged.append(current.human_id)
            continue
        if _diverged(current):
            outcome.kept.append(
                {
                    "human_id": current.human_id,
                    "source_human_id": human_id,
                    "this_bid": str(current.net_quantity),
                    "published": str(source["net_quantity"]),
                    "reason": "edited or rejected in this bid; the published item has changed",
                }
            )
            continue
        apply_transition(
            session,
            current,
            target=QtoItemState.SUPERSEDED,
            actor=SYSTEM_ACTOR,
            reason=f"the project's shared takeoff v{shared.version} replaces it",
        )
        outcome.updated.append(_create(session, bid, shared, source, actor, current).human_id)
    for human_id, current in adopted.items():
        if human_id in seen:
            continue
        if _diverged(current):
            outcome.kept.append(
                {
                    "human_id": current.human_id,
                    "source_human_id": human_id,
                    "this_bid": str(current.net_quantity),
                    "published": None,
                    "reason": "edited or rejected in this bid; no longer in the published takeoff",
                }
            )
            continue
        apply_transition(
            session,
            current,
            target=QtoItemState.SUPERSEDED,
            actor=SYSTEM_ACTOR,
            reason=f"no longer in the project's shared takeoff (v{shared.version})",
        )
        outcome.withdrawn.append(current.human_id)
    session.flush()
    qto.completeness(session, bid.id)
    record_event(
        session,
        context=AuditContext(organisation_id=bid.organisation_id, bid_id=bid.id),
        actor=actor,
        action="shared takeoff: adopted",
        entity_type=SharedTakeoff.__tablename__,
        entity_id=shared.id,
        after={
            "version": shared.version,
            "source_bid": shared.source_bid_human_id,
            "created": len(outcome.created),
            "updated": len(outcome.updated),
            "unchanged": len(outcome.unchanged),
            "kept": [row["human_id"] for row in outcome.kept],
            "withdrawn": outcome.withdrawn,
        },
    )
    return outcome


def status(session: Session, bid: Bid) -> dict[str, Any]:
    """Where a bid stands with its project's shared takeoff."""
    available = published(session, bid.project_id)
    adopted = [i for i in qto.live_items(session, bid.id) if qto.is_adopted(i)]
    versions = sorted(
        {int(dict(i.derivation)["shared"]["version"]) for i in adopted}  # type: ignore[index]
    )
    return {
        "project_id": str(bid.project_id),
        "published": [
            {
                "id": str(row.id),
                "version": row.version,
                "source_bid": row.source_bid_human_id,
                "published_by": row.published_by,
                "published_at": row.created_at,
                "items": len(row.items),
                "mine": row.source_bid_id == bid.id,
            }
            for row in available
        ],
        "adopted_items": len(adopted),
        "adopted_versions": versions,
        "edited_here": sorted(i.human_id for i in adopted if _diverged(i)),
        "behind": bool(adopted) and bool(available) and versions != [available[0].version],
    }
