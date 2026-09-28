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
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.models.takeoff import DetectedObject, DuplicateGroup, PipeRun, QtoItem
from firebid.services import qto

HIGH, MEDIUM = 0.9, 0.6


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
