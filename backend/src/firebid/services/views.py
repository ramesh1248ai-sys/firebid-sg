"""Each sheet's views, their scales, and the three things done with them (FR-VIS-05/07/08).

Views are detected straight after geometry, in the parse job: the Parquet file is already
there, and nothing here opens the tender file. Then:

* **measure**: a length along points on a view, refused unless the view's scale is verified
  or calibrated (FR-VIS-05). The refusal says why and what would unlock it.
* **calibrate**: a person gives two points and the real distance between them. The view
  becomes `calibrated`, with who did it and when, and an audit event.
* **check against the grid**: a view that only states its scale is verified where the grid
  it shares with a proved view agrees (`check_against_grid`). One calibration, or one
  dimensioned floor, so serves every sheet drawn on the same gridlines.
* **locate**: a point on a sheet as an estimator says it: the view it is in, its grid
  reference, its level and zone (FR-VIS-07).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from firebid.db.audit import record_event
from firebid.db.models.core import Bid
from firebid.db.models.documents import Sheet, SheetRevision
from firebid.db.models.drawings import SheetGeometry, SheetView
from firebid.domain.actors import Actor, AuditContext
from firebid.drawings import scale, views
from firebid.drawings.grids import GridSystem
from firebid.services import geometry as geometry_service
from firebid.storage.object_store import ObjectStore

log = structlog.get_logger("firebid.views")


def detect_sheet(session: Session, store: ObjectStore, record: SheetGeometry) -> list[SheetView]:
    """The sheet's views, detected again unless this detector has already seen this geometry.

    A calibration is a person's work, so it is carried over to the view that is still there:
    same place in the order, same kind.
    """
    existing = list(
        session.execute(
            select(SheetView)
            .where(SheetView.sheet_id == record.sheet_id)
            .order_by(SheetView.ordinal)
        ).scalars()
    )
    if existing and all(view.detector_version == views.DETECTOR_VERSION for view in existing):
        return existing

    revision = _revision_of(session, record.sheet_id)
    sheet_scale = revision.scale_text if revision else None
    table = geometry_service.load(store, record)
    page = (record.page[0], record.page[1], record.page[2], record.page[3])
    found = views.analyse(table, page, sheet_scale, record.views)

    kept = {(view.ordinal, view.kind): view for view in existing if view.calibration}
    session.execute(delete(SheetView).where(SheetView.sheet_id == record.sheet_id))
    session.flush()
    rows = []
    for ordinal, item in enumerate(found):
        row = _row(record, ordinal, item, revision)
        previous = kept.get((ordinal, str(item.view.kind)))
        if previous is not None:
            _carry_calibration(previous, row)
        session.add(row)
        rows.append(row)
    session.flush()
    log.info(
        "views_detected",
        sheet_id=str(record.sheet_id),
        views=[(row.kind, row.scale_status) for row in rows],
    )
    return rows


def detect_all(session: Session, store: ObjectStore, sheets: list[Sheet]) -> list[SheetView]:
    records = session.execute(
        select(SheetGeometry).where(SheetGeometry.sheet_id.in_([sheet.id for sheet in sheets]))
    ).scalars()
    found: list[SheetView] = []
    for record in records:
        found.extend(detect_sheet(session, store, record))
    return found


def _revision_of(session: Session, sheet_id: Any) -> SheetRevision | None:
    return (
        session.execute(
            select(SheetRevision)
            .where(SheetRevision.sheet_id == sheet_id)
            .order_by(SheetRevision.created_at.desc())
        )
        .scalars()
        .first()
    )


def _row(
    record: SheetGeometry, ordinal: int, item: views.Analysed, revision: SheetRevision | None
) -> SheetView:
    view, verdict = item.view, item.verdict
    grid_box = item.grid.box(view.extent) if item.grid else None
    return SheetView(
        bid_id=record.bid_id,
        sheet_id=record.sheet_id,
        ordinal=ordinal,
        kind=str(view.kind),
        title=view.title and view.title[:300],
        source=view.source,
        extent=[round(value, 3) for value in view.extent],
        # The view's own title says which level it shows; the drawing number is the fallback.
        level=view.level or (revision.level if revision else None),
        stated_scale=view.stated.text and view.stated.text[:80],
        stated_denominator=view.stated.denominator,
        scale_status=str(verdict.status),
        denominator=verdict.denominator,
        scale_evidence=verdict.as_json(),
        grid=item.grid.as_json() if item.grid else None,
        grid_box=[round(value, 4) for value in grid_box] if grid_box else None,
        grid_marks=item.marks,
        detector_version=views.DETECTOR_VERSION,
    )


def _carry_calibration(previous: SheetView, row: SheetView) -> None:
    row.calibration = previous.calibration
    row.calibrated_by = previous.calibrated_by
    row.calibrated_by_id = previous.calibrated_by_id
    row.calibrated_at = previous.calibrated_at
    if row.scale_status != str(scale.ScaleStatus.NTS) and previous.calibration:
        row.scale_status = str(scale.ScaleStatus.CALIBRATED)
        row.denominator = float(str(previous.calibration["denominator"]))


# --- What is done with a view ---------------------------------------------------------------


def verdict_of(view: SheetView) -> scale.Verdict:
    evidence = view.scale_evidence or {}
    return scale.Verdict(
        status=scale.ScaleStatus(view.scale_status),
        denominator=view.denominator,
        stated=scale.Stated(view.stated_denominator, view.scale_status == "nts", view.stated_scale),
        reason=str(evidence.get("reason", "")),
    )


def measure(view: SheetView, points: list[tuple[float, float]]) -> float:
    """A length in millimetres along sheet points. Raises `scale.NotMeasurable` if refused."""
    return scale.measure(points, verdict_of(view))


def calibrate(
    session: Session,
    view: SheetView,
    points: tuple[tuple[float, float], tuple[float, float]],
    distance_mm: float,
    actor: Actor,
) -> SheetView:
    """A person's calibration of a view. Refused on a view marked not to scale: a schematic
    has no one scale to find, and calibrating it would make its lengths look real."""
    if view.scale_status == str(scale.ScaleStatus.NTS):
        raise ValueError(
            "this view is marked not to scale, so it cannot be calibrated; take its "
            "quantities from a plan"
        )
    verdict = scale.calibrated(points, distance_mm, verdict_of(view).stated)
    before = {"scale_status": view.scale_status, "denominator": view.denominator}
    now = datetime.now(UTC)
    view.scale_status = str(scale.ScaleStatus.CALIBRATED)
    view.denominator = verdict.denominator
    view.calibration = {
        "points": [list(points[0]), list(points[1])],
        "distance_mm": distance_mm,
        "denominator": verdict.denominator,
        "previous": before,
    }
    view.calibrated_by = actor.label[:200]
    view.calibrated_by_id = actor.id
    view.calibrated_at = now
    organisation_id = session.execute(
        select(Bid.organisation_id).where(Bid.id == view.bid_id)
    ).scalar_one()
    record_event(
        session,
        context=AuditContext(organisation_id=organisation_id, bid_id=view.bid_id),
        actor=actor,
        action="view: calibrated",
        entity_type=SheetView.__tablename__,
        entity_id=view.id,
        before=before,
        after={"scale_status": view.scale_status, "denominator": view.denominator},
    )
    session.flush()
    # What a person measured here is now known of every sheet on the same gridlines.
    check_against_grid(session, view.bid_id)
    return view


# What the grid decided is kept beside what the view's own dimensions said, under this key
# of `scale_evidence`, so it can be decided again when the proved views change.
OWN = "own"


def check_against_grid(session: Session, bid_id: Any) -> list[SheetView]:
    """Check every view that only states its scale against the grid of the proved views.

    Proved: verified by its own dimensions, or calibrated by a person. The real distance
    between two gridlines such a view shows is a known dimension (FR-VIS-05), and a view on
    the same gridlines whose stated scale agrees with it is verified, with the spacings and
    the sheet they are known from as its evidence. The grid only ever verifies: it does not
    make a view conflicting. Decided afresh each time, from what each
    view's own dimensions said: a view the grid verified goes back to unverified when the
    view that proved it is gone. Returns the views whose status changed.
    """
    rows = list(
        session.execute(
            select(SheetView)
            .where(SheetView.bid_id == bid_id, SheetView.grid_marks.is_not(None))
            .order_by(SheetView.sheet_id, SheetView.ordinal)
        ).scalars()
    )
    if not rows:
        return []
    numbers: dict[Any, str] = {}
    for sheet_id, number in session.execute(
        select(SheetRevision.sheet_id, SheetRevision.sheet_number)
        .where(SheetRevision.sheet_id.in_({row.sheet_id for row in rows}))
        .order_by(SheetRevision.created_at)
    ):
        if number:
            numbers[sheet_id] = number

    def own(row: SheetView) -> dict[str, Any]:
        evidence: dict[str, Any] = dict(row.scale_evidence or {})
        kept = evidence.get(OWN)
        return dict(kept) if isinstance(kept, dict) else evidence

    calibrated, verified = str(scale.ScaleStatus.CALIBRATED), str(scale.ScaleStatus.VERIFIED)
    proved = []
    for row in rows:
        if row.scale_status == calibrated and row.denominator:
            denominator = row.denominator
        elif own(row).get("status") == verified and own(row).get("denominator"):
            denominator = float(own(row)["denominator"])
        else:
            continue
        name = numbers.get(row.sheet_id) or "another sheet"
        proved.append((name, denominator, row.grid_marks or {}))
    # In the sheets' order, so which sheet is named as the proof does not change by chance.
    known = scale.known_spacings(sorted(proved, key=lambda item: item[0]))

    changed = []
    for row in rows:
        mine = own(row)
        if row.scale_status == calibrated or mine.get("status") != str(
            scale.ScaleStatus.UNVERIFIED
        ):
            continue
        decided = scale.corroborate(
            scale.Verdict.from_json(mine), scale.grid_evidence(row.grid_marks or {}, known)
        )
        evidence = mine if decided is None else decided.as_json() | {OWN: mine}
        status = str(mine["status"]) if decided is None else str(decided.status)
        if status != row.scale_status:
            changed.append(row)
        row.scale_status = status
        row.denominator = None if decided is None else decided.denominator
        if evidence != row.scale_evidence:
            row.scale_evidence = evidence
    session.flush()
    if changed:
        log.info(
            "views_checked_against_grid",
            bid_id=str(bid_id),
            changed=[(numbers.get(row.sheet_id), row.scale_status) for row in changed],
        )
    return changed


@dataclass(frozen=True)
class Location:
    view: SheetView | None
    grid_reference: str | None
    grid_index: tuple[float, float] | None
    level: str | None
    zone: str | None


def locate(session: Session, sheet: Sheet, x: float, y: float) -> Location:
    """Where a sheet point is on the building: its view, grid reference, level and zone."""
    sheet_views = list(
        session.execute(
            select(SheetView).where(SheetView.sheet_id == sheet.id).order_by(SheetView.ordinal)
        ).scalars()
    )
    containing = [view for view in sheet_views if _contains(view.extent, x, y)]
    # The smallest view holding the point: a key plan inside a plan's extent wins.
    view = min(containing, key=lambda item: _area(item.extent), default=None)
    revision = _revision_of(session, sheet.id)
    grid = GridSystem.from_json(view.grid) if view is not None and view.grid else None  # type: ignore[arg-type]
    return Location(
        view=view,
        grid_reference=grid.reference(x, y) if grid else None,
        grid_index=grid.index(x, y) if grid else None,
        level=(view.level if view is not None else None) or (revision.level if revision else None),
        zone=revision.zone if revision else None,
    )


def _contains(extent: list[float], x: float, y: float) -> bool:
    return extent[0] <= x <= extent[2] and extent[1] <= y <= extent[3]


def _area(extent: list[float]) -> float:
    return (extent[2] - extent[0]) * (extent[3] - extent[1])
