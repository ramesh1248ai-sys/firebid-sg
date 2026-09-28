"""The verification workbench (P1-08): what is drawn over a sheet, and how items are reviewed.

The overlay is every detection, pipe run and manual measurement on one sheet, each with the
QTO item it counts towards and that item's status, so the viewer can colour it by status and
confidence (FR-REV-01) and open the item it belongs to.

A detection belongs to the live item whose members list it. One that belongs to none is
either repeated on another sheet (a member of a duplicate group, not counted here), rejected
by a person, or not taken off at all (an object type takeoff does not count).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.models.takeoff import DetectedObject, DuplicateGroup, PipeRun, QtoItem
from firebid.services import qto

HIGH, MEDIUM = 0.9, 0.6
CONFIG = Path(__file__).resolve().parents[3] / "config" / "review.yaml"
DONE = ("verified", "baselined")


@lru_cache(maxsize=2)
def settings(path: Path = CONFIG) -> dict[str, Any]:
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def reason_codes() -> dict[str, str]:
    return dict(settings().get("reason_codes") or {})


def band(confidence: float | None) -> str:
    if confidence is None:
        return "low"
    return "high" if confidence >= HIGH else "medium" if confidence >= MEDIUM else "low"


def status_of(item: QtoItem) -> str:
    """How the overlay colours an item: its state, or `manual` for a person's own item."""
    if item.is_manual and item.state not in ("rejected", "verified"):
        return "manual"
    return item.state


@dataclass
class Mark:
    """One thing drawn on the sheet, in sheet millimetres."""

    id: str
    kind: str  # detection | run | manual
    object_type: str
    box: tuple[float, float, float, float]
    status: str
    confidence: float | None
    item_id: str | None
    item_human_id: str | None
    x: float | None = None
    y: float | None = None
    points: list[list[float]] = field(default_factory=list)
    label: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "object_type": self.object_type,
            "box": [round(v, 3) for v in self.box],
            "status": self.status,
            "band": band(self.confidence),
            "confidence": self.confidence,
            "item_id": self.item_id,
            "item_human_id": self.item_human_id,
            "x": self.x,
            "y": self.y,
            "points": self.points,
            "label": self.label,
        }


def _box_of(points: list[list[float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def overlay(session: Session, bid_id: uuid.UUID, sheet_id: uuid.UUID) -> list[Mark]:
    """Everything drawn over one sheet, with the item and status each belongs to."""
    items = qto.live_items(session, bid_id)
    owner: dict[str, QtoItem] = {}
    for item in items:
        derivation: dict[str, Any] = dict(item.derivation or {})
        for member in derivation.get("members") or []:
            if isinstance(member, dict) and member.get("id"):
                owner.setdefault(str(member["id"]), item)
    repeated = {
        str(member["id"])
        for group in session.execute(
            select(DuplicateGroup).where(DuplicateGroup.bid_id == bid_id)
        ).scalars()
        if group.status != "not_duplicate"
        for member in group.members
        if not member.get("keep")
    }

    def status(mark_id: str, state: str) -> tuple[str, QtoItem | None]:
        item = owner.get(mark_id)
        if item is not None:
            return status_of(item), item
        if state == "rejected":
            return "rejected", None
        return ("duplicate" if mark_id in repeated else "not_taken_off"), None

    marks: list[Mark] = []
    for row in session.execute(
        select(DetectedObject).where(
            DetectedObject.bid_id == bid_id, DetectedObject.sheet_id == sheet_id
        )
    ).scalars():
        position: dict[str, Any] = dict(row.geometry_ref or {})
        x, y = float(position.get("x", 0.0)), float(position.get("y", 0.0))
        box = position.get("box") or [x - 1, y - 1, x + 1, y + 1]
        state, owned = status(str(row.id), row.state)
        marks.append(
            Mark(
                id=str(row.id),
                kind="detection",
                object_type=row.object_type if row.kind == "object" else row.kind,
                box=(float(box[0]), float(box[1]), float(box[2]), float(box[3])),
                status=state,
                confidence=row.confidence,
                item_id=str(owned.id) if owned else None,
                item_human_id=owned.human_id if owned else None,
                x=x,
                y=y,
            )
        )
    for run in session.execute(
        select(PipeRun).where(PipeRun.bid_id == bid_id, PipeRun.sheet_id == sheet_id)
    ).scalars():
        points = [[float(p[0]), float(p[1])] for p in run.points]
        state, owned = status(str(run.id), run.state)
        marks.append(
            Mark(
                id=str(run.id),
                kind="run",
                object_type=f"pipe_{run.run_class}",
                box=_box_of(points),
                status=state,
                confidence=run.confidence,
                item_id=str(owned.id) if owned else None,
                item_human_id=owned.human_id if owned else None,
                points=points,
                label=f"DN{run.nominal_dn}" if run.nominal_dn else None,
            )
        )
    for item in items:
        measurement = dict(item.derivation or {}).get("measurement")
        if not item.is_manual or not isinstance(measurement, dict):
            continue
        if measurement.get("sheet_id") != str(sheet_id):
            continue
        points = [[float(p[0]), float(p[1])] for p in measurement.get("points", [])]
        if not points:
            continue
        if measurement.get("kind") == "marks":
            for index, (x, y) in enumerate(points):
                marks.append(
                    Mark(
                        id=f"manual:{item.id}:{index}",
                        kind="manual",
                        object_type=item.item_type,
                        box=(x - 1.5, y - 1.5, x + 1.5, y + 1.5),
                        status=status_of(item),
                        confidence=item.confidence,
                        item_id=str(item.id),
                        item_human_id=item.human_id,
                        x=x,
                        y=y,
                        label=item.description,
                    )
                )
            continue
        marks.append(
            Mark(
                id=f"manual:{item.id}",
                kind="manual",
                object_type=item.item_type,
                box=_box_of(points),
                status=status_of(item),
                confidence=item.confidence,
                item_id=str(item.id),
                item_human_id=item.human_id,
                points=points,
                label=item.description,
            )
        )
    return marks


def evidence_boxes(item: QtoItem) -> list[dict[str, Any]]:
    """Where an item's evidence is, per sheet: what the viewer zooms to (NFR-10)."""
    derivation: dict[str, Any] = dict(item.derivation or {})
    by_number = {s.get("sheet_number"): s.get("sheet_id") for s in derivation.get("sources") or []}
    boxes: dict[str, list[float]] = {}
    for mark in derivation.get("geometry") or []:
        sheet_id = by_number.get(mark.get("sheet"))
        if sheet_id is None:
            continue
        points = mark.get("points") or (
            [[mark["x"], mark["y"]]] if mark.get("x") is not None else []
        )
        for point in points:
            box = boxes.setdefault(sheet_id, [point[0], point[1], point[0], point[1]])
            box[0], box[1] = min(box[0], point[0]), min(box[1], point[1])
            box[2], box[3] = max(box[2], point[0]), max(box[3], point[1])
    return [{"sheet_id": sheet, "box": box} for sheet, box in boxes.items()]


def sheets(session: Session, bid_id: uuid.UUID) -> list[dict[str, Any]]:
    """The Current sheets takeoff reads, by drawing number: what the workbench offers."""
    found = qto.current_sheets(session, bid_id)
    return sorted(
        (
            {
                "sheet_id": str(info.sheet.id),
                "sheet_number": info.number,
                "revision": info.revision.revision_label,
                "title": info.revision.title,
                "level": info.level,
                "width_mm": info.sheet.width_mm,
                "height_mm": info.sheet.height_mm,
                "views": [
                    {
                        "id": str(view.id),
                        "kind": view.kind,
                        "extent": list(view.extent),
                        "scale_status": view.scale_status,
                        "denominator": view.denominator,
                        "measurable": view.scale_status in qto.MEASURABLE,
                    }
                    for view in sorted(info.views.values(), key=lambda v: v.ordinal)
                ],
            }
            for info in found.values()
        ),
        key=lambda s: str(s["sheet_number"]),
    )


# --- The review queue (FR-REV-02) -----------------------------------------------------------


def weight_of(item: QtoItem) -> float:
    """Value per unit of the item's class, until rates exist (P1-10)."""
    weights: dict[str, float] = {
        str(k): float(v) for k, v in (settings().get("impact_weights") or {}).items()
    }
    for key in (item.classification, item.item_type):
        if key and key in weights:
            return weights[key]
    return weights.get("default", 1.0)


def impact(item: QtoItem) -> float:
    """What the item is worth to the bid: quantity x rate, or x the class weight for now."""
    return float(item.net_quantity) * weight_of(item)


def risk(item: QtoItem) -> float:
    """(1 - calibrated confidence) x impact: what a mistake here would cost, weighted by how
    likely one is. A manual item is a person's own and scores its impact alone."""
    confidence = item.confidence if item.confidence is not None else 0.0
    return (1.0 - max(0.0, min(1.0, confidence))) * impact(item)


@dataclass
class QueueRow:
    item: QtoItem
    risk: float
    impact: float
    sheet_ids: list[str]
    system: str


def system_of(item: QtoItem) -> str:
    stated: dict[str, Any] = dict(item.attributes or {})
    system = stated.get("system")
    if isinstance(system, dict):
        system = system.get("value")
    return str(system or "sprinkler")


def queue(
    session: Session,
    bid_id: uuid.UUID,
    *,
    sheet_id: str | None = None,
    system: str | None = None,
    level: str | None = None,
    item_type: str | None = None,
    status: str | None = None,
) -> list[QueueRow]:
    """Every live item, riskiest first. Items still to decide come before decided ones."""
    rows = []
    for item in qto.live_items(session, bid_id):
        derivation: dict[str, Any] = dict(item.derivation or {})
        sheets = [str(s.get("sheet_id")) for s in derivation.get("sources") or []]
        row = QueueRow(item, risk(item), impact(item), sheets, system_of(item))
        if sheet_id and sheet_id not in sheets:
            continue
        if system and row.system != system:
            continue
        if level and item.level != level:
            continue
        if item_type and item.item_type != item_type:
            continue
        if status and status_of(item) != status:
            continue
        rows.append(row)
    return sorted(
        rows,
        key=lambda r: (r.item.state in (*DONE, "rejected"), -r.risk, r.item.human_id),
    )


# --- Coverage (FR-REV-04) -------------------------------------------------------------------


def coverage(session: Session, bid_id: uuid.UUID) -> dict[str, Any]:
    """The share of the takeoff a person has verified, by item count and by value.

    Rejected items are decided and leave both counts. Value is weighted by class until rates
    exist (P1-10), and says so.
    """
    items = [i for i in qto.live_items(session, bid_id) if i.state != "rejected"]
    verified = [i for i in items if i.state in DONE]
    value_total = sum(impact(i) for i in items)
    value_verified = sum(impact(i) for i in verified)
    policy = float((settings().get("coverage_policy") or {}).get("items_percent", 100))
    items_percent = 100.0 * len(verified) / len(items) if items else 0.0
    return {
        "items_total": len(items),
        "items_verified": len(verified),
        "items_percent": round(items_percent, 2),
        "value_total": round(value_total, 3),
        "value_verified": round(value_verified, 3),
        "value_percent": round(100.0 * value_verified / value_total, 2) if value_total else 0.0,
        "value_basis": "weighted by item class (no rates yet)",
        "policy_percent": policy,
        "met": bool(items) and items_percent >= policy,
    }


def unmapped_in_scope(session: Session, bid_id: uuid.UUID) -> list[dict[str, Any]]:
    """Symbols on Current sheets nobody has said what they are (they are counted as nothing)."""
    from firebid.services import symbols

    found = symbols.counts(session, bid_id, current_only=True)
    return [
        {
            "symbol_key": group.symbol_key,
            "description": group.description,
            "instances": group.instances,
            "status": group.status,
            "sheets": sorted(str(s) for s in group.sheets),
            "mapping_lineage_id": str(group.mapping_lineage_id)
            if group.mapping_lineage_id
            else None,
        }
        for group in found.unmapped
    ]
