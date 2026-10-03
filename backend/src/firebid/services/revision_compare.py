"""Comparing two revisions of a drawing, from what is stored of each (FR-DOC-08).

Each revision of a drawing is a sheet of its own, with its own detections, pipe runs, views
and page. The comparison is `drawings.revision_diff`'s: this module gives it both sheets'
elements, grid lines and frames, and says which revision a sheet is compared with when
nobody chose: the one it superseded.

A person's "not there" is left out on both sides, and so is what the design rules proposed:
the comparison is of what the two drawings show.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.models.documents import Sheet, SheetRevision
from firebid.db.models.drawings import SheetView
from firebid.db.models.takeoff import DetectedObject, PipeRun
from firebid.drawings import revision_diff
from firebid.drawings.revision_diff import Diff, Element

NOT_COMPARED = ("run_class", "vertical_not_drawn")


class CompareError(ValueError):
    """A comparison that cannot be made, with the reason a person can act on."""


@dataclass
class Side:
    sheet_id: uuid.UUID
    sheet_number: str | None
    revision: str | None
    state: str | None
    elements: list[Element]
    grid: revision_diff.GridLines | None
    frame: revision_diff.Frame | None

    def as_json(self) -> dict[str, Any]:
        return {
            "sheet_id": str(self.sheet_id),
            "sheet_number": self.sheet_number,
            "revision": self.revision,
            "state": self.state,
            "elements": len(self.elements),
        }


@dataclass
class Comparison:
    old: Side
    new: Side
    diff: Diff


def revision_of(session: Session, sheet_id: uuid.UUID) -> SheetRevision | None:
    return (
        session.execute(
            select(SheetRevision)
            .where(SheetRevision.sheet_id == sheet_id)
            .order_by(SheetRevision.created_at.desc())
        )
        .scalars()
        .first()
    )


def revisions(session: Session, bid_id: uuid.UUID, sheet_id: uuid.UUID) -> list[SheetRevision]:
    """Every revision of the drawing this sheet is one of, newest first."""
    own = revision_of(session, sheet_id)
    if own is None or not own.sheet_number:
        return [own] if own else []
    return list(
        session.execute(
            select(SheetRevision)
            .where(SheetRevision.bid_id == bid_id, SheetRevision.sheet_number == own.sheet_number)
            .order_by(SheetRevision.created_at.desc())
        ).scalars()
    )


def superseded_by(session: Session, revision: SheetRevision) -> SheetRevision | None:
    """The revision this one replaced: the latest that names it as its successor."""
    return (
        session.execute(
            select(SheetRevision)
            .where(SheetRevision.superseded_by_id == revision.id)
            .order_by(SheetRevision.created_at.desc())
        )
        .scalars()
        .first()
    )


def side(session: Session, bid_id: uuid.UUID, sheet_id: uuid.UUID) -> Side:
    sheet = session.get(Sheet, sheet_id)
    if sheet is None or sheet.bid_id != bid_id:
        raise CompareError("no such sheet on this bid")
    elements = [
        Element(
            str(row.id),
            row.kind,
            row.object_type,
            float((row.geometry_ref or {}).get("x", 0.0)),  # type: ignore[arg-type]
            float((row.geometry_ref or {}).get("y", 0.0)),  # type: ignore[arg-type]
            {k: v for k, v in (row.attributes or {}).items() if k not in NOT_COMPARED},
        )
        for row in session.execute(
            select(DetectedObject)
            .where(
                DetectedObject.bid_id == bid_id,
                DetectedObject.sheet_id == sheet_id,
                DetectedObject.kind != "drop",
                DetectedObject.state != "rejected",
                DetectedObject.extraction_method != "designed",
            )
            .order_by(DetectedObject.id)
        ).scalars()
    ]
    for run in session.execute(
        select(PipeRun)
        .where(
            PipeRun.bid_id == bid_id,
            PipeRun.sheet_id == sheet_id,
            PipeRun.origin == "detected",
            PipeRun.state != "rejected",
        )
        .order_by(PipeRun.run_index)
    ).scalars():
        points = tuple((float(p[0]), float(p[1])) for p in run.points)
        if len(points) < 2:
            continue
        elements.append(
            Element(
                str(run.id),
                "run",
                f"pipe_{run.run_class}",
                (points[0][0] + points[-1][0]) / 2,
                (points[0][1] + points[-1][1]) / 2,
                {"dn": run.nominal_dn},
                points,
                run.length_mm,
            )
        )
    gridded = [
        view
        for view in session.execute(
            select(SheetView).where(SheetView.sheet_id == sheet_id)
        ).scalars()
        if view.grid
    ]
    largest = max(
        gridded,
        key=lambda v: (v.extent[2] - v.extent[0]) * (v.extent[3] - v.extent[1]),
        default=None,
    )
    revision = revision_of(session, sheet_id)
    frame = (
        (0.0, 0.0, float(sheet.width_mm), float(sheet.height_mm))
        if sheet.width_mm and sheet.height_mm
        else None
    )
    return Side(
        sheet_id=sheet_id,
        sheet_number=revision.sheet_number if revision else None,
        revision=revision.revision_label if revision else None,
        state=revision.state if revision else None,
        elements=elements,
        grid=revision_diff.grid_lines(largest.grid) if largest else None,  # type: ignore[arg-type]
        frame=frame,
    )


def compare(
    session: Session,
    bid_id: uuid.UUID,
    sheet_id: uuid.UUID,
    against_sheet_id: uuid.UUID | None = None,
) -> Comparison:
    """What this sheet changed from another revision of its drawing: the one named, or the
    one it superseded."""
    if against_sheet_id is None:
        own = revision_of(session, sheet_id)
        earlier = superseded_by(session, own) if own else None
        if earlier is None:
            raise CompareError("this sheet superseded no earlier revision; name one to compare")
        against_sheet_id = earlier.sheet_id
    old, new = side(session, bid_id, against_sheet_id), side(session, bid_id, sheet_id)
    if old.sheet_number and new.sheet_number and old.sheet_number != new.sheet_number:
        raise CompareError(
            f"{old.sheet_number} and {new.sheet_number} are different drawings, not revisions"
        )
    found = revision_diff.diff(
        old.elements,
        new.elements,
        old_grid=old.grid,
        new_grid=new.grid,
        old_frame=old.frame,
        new_frame=new.frame,
    )
    return Comparison(old, new, found)
