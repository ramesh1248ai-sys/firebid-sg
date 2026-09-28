"""Detections for a bid's sheets: symbols, risers, drops and pipe runs, as proposals (P1-05).

Runs in the parse job after symbols are read, and again for a whole bid whenever a symbol
mapping is confirmed or changed: what a symbol is decides whether it is detected at all, so
a confirmation must reach the detections. Nothing here opens the tender file; it works from
the stored geometry, views, legend rows, symbol instances and confirmed mappings.

Only confirmed mappings produce detections. An unmapped or merely proposed symbol is not
detected; it is listed as unmapped by `services.symbols.counts` (FR-VIS-02).

Every detection is written with its method, evidence, view and grid reference, or `gaps`
saying why the sheet cannot give them; the database refuses anything else (migration 0021).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
import yaml
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from firebid.db.models.documents import Sheet, SheetRevision
from firebid.db.models.drawings import SheetGeometry, SheetView
from firebid.db.models.symbols import ConsultantProfile, LegendEntry, SymbolInstance
from firebid.db.models.takeoff import DetectedObject, PipeRun
from firebid.drawings import calibration as calibration_maps
from firebid.drawings import symbols
from firebid.drawings.detection import (
    DETECTOR_VERSION,
    Detected,
    DetectedRun,
    SheetDetections,
    ViewInfo,
    detect,
)
from firebid.drawings.grids import GridSystem
from firebid.drawings.pipe_network import Placed, Profile
from firebid.drawings.symbols import Signature
from firebid.services import geometry as geometry_service
from firebid.services import object_library
from firebid.services import symbols as symbol_service
from firebid.storage.object_store import ObjectStore

log = structlog.get_logger("firebid.detection")

CONFIG = Path(__file__).resolve().parents[3] / "config" / "detection.yaml"
MEASURABLE = ("verified", "calibrated")


@lru_cache(maxsize=2)
def settings(path: Path = CONFIG) -> dict[str, Any]:
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


@dataclass
class SheetOutcome:
    sheet_id: uuid.UUID
    objects: int
    runs: int
    skipped_unconfirmed: int


def profile_for(
    session: Session, organisation_id: uuid.UUID, consultant_key: str
) -> Profile | None:
    latest = (
        session.execute(
            select(ConsultantProfile)
            .where(
                ConsultantProfile.organisation_id == organisation_id,
                ConsultantProfile.consultant_key == consultant_key,
            )
            .order_by(ConsultantProfile.version.desc())
        )
        .scalars()
        .first()
    )
    if latest is None:
        return None
    return Profile(
        tuple(latest.pipe_layers or ()), tuple(int(c) for c in latest.pipe_colours or ())
    )


def _views(session: Session, sheet_id: uuid.UUID) -> list[ViewInfo]:
    rows = session.execute(
        select(SheetView).where(SheetView.sheet_id == sheet_id).order_by(SheetView.ordinal)
    ).scalars()
    return [
        ViewInfo(
            id=view.id,
            extent=(view.extent[0], view.extent[1], view.extent[2], view.extent[3]),
            denominator=view.denominator if view.scale_status in MEASURABLE else None,
            grid=GridSystem.from_json(view.grid) if view.grid else None,  # type: ignore[arg-type]
            level=view.level,
            kind=view.kind,
        )
        for view in rows
    ]


def _placed(
    session: Session,
    table: Any,
    sheet_id: uuid.UUID,
    organisation_id: uuid.UUID,
    excluded: list[tuple[float, float, float, float]],
) -> tuple[list[Placed], int]:
    """The sheet's installed symbols whose mapping a person has confirmed."""
    kinds = {kind.key: kind for kind in object_library.current(session, organisation_id)}
    entries = {
        entry.id: entry
        for entry in session.execute(
            select(LegendEntry).where(LegendEntry.bid_id == _bid_of(session, sheet_id))
        ).scalars()
    }
    clusters = {
        (round(c.centre[0], 2), round(c.centre[1], 2)): c
        for c in symbols.clusters(table, excluding=excluded)
    }
    placed: list[Placed] = []
    skipped = 0
    for instance in session.execute(
        select(SymbolInstance).where(SymbolInstance.sheet_id == sheet_id)
    ).scalars():
        entry = entries.get(instance.legend_entry_id) if instance.legend_entry_id else None
        lineage = instance.mapping_lineage_id or (entry.mapping_lineage_id if entry else None)
        mapping = symbol_service.current(session, lineage) if lineage else None
        kind = kinds.get(mapping.object_type_key or "") if mapping else None
        if mapping is None or mapping.state != "confirmed" or kind is None:
            skipped += 1
            continue
        cluster = clusters.get((round(instance.cx, 2), round(instance.cy, 2)))
        signature = Signature.from_json(instance.signature)
        reference = Signature.from_json(entry.signature if entry else mapping.signature)
        rotation = instance.rotation
        if rotation is None:
            rotation = symbols.orientation(signature, reference)
        by_block = bool(signature.block_hash) and signature.block_hash == reference.block_hash
        placed.append(
            Placed(
                object_type=kind.key,
                category=kind.category,
                measure=kind.measure,
                cx=instance.cx,
                cy=instance.cy,
                box=(instance.bbox[0], instance.bbox[1], instance.bbox[2], instance.bbox[3]),
                rotation=rotation,
                method="block_hash" if by_block else "shape",
                match_distance=float(instance.match_distance or 0.0),
                tolerance=reference.tolerance,
                attributes=dict(mapping.attributes or {}),
                rows=cluster.rows if cluster else (),
                description=entry.description if entry else mapping.description,
                instance_id=instance.id,
            )
        )
    return placed, skipped


def _bid_of(session: Session, sheet_id: uuid.UUID) -> uuid.UUID:
    sheet = session.get(Sheet, sheet_id)
    if sheet is None:
        raise ValueError("no such sheet")
    return sheet.bid_id


def _gaps(item: Detected | DetectedRun, has_grid: bool) -> dict[str, str]:
    gaps: dict[str, str] = {}
    if item.view is None:
        gaps["view"] = "outside every view on the sheet"
    if item.grid_reference is None:
        gaps["grid_reference"] = (
            "outside the structural grid" if has_grid else "the view shows no structural grid"
        )
    if isinstance(item, DetectedRun) and item.length_mm is None:
        gaps["length"] = "the view's scale is not verified or calibrated (FR-VIS-05)"
    return gaps


def detect_sheet(session: Session, store: ObjectStore, record: SheetGeometry) -> SheetOutcome:
    """Detect one sheet again from its stored geometry and confirmed mappings."""
    from firebid.drawings.geometry import texts
    from firebid.drawings.views import _title_block_region as title_block

    sheet = session.get(Sheet, record.sheet_id)
    if sheet is None:
        raise ValueError("no such sheet")
    consultant = symbol_service.consultant_of(session, sheet.bid_id)
    table = geometry_service.load(store, record)
    page = (record.page[0], record.page[1], record.page[2], record.page[3])
    excluded = [
        (e.row_box[0], e.row_box[1], e.row_box[2], e.row_box[3])
        for e in session.execute(
            select(LegendEntry).where(LegendEntry.sheet_id == sheet.id)
        ).scalars()
    ]
    region = title_block(texts(table), page)
    if region is not None:
        excluded.append((region.x0, region.y0, region.x1, region.y1))
    views = _views(session, sheet.id)
    placed, skipped = _placed(session, table, sheet.id, consultant.organisation_id, excluded)
    found = detect(
        table,
        placed,
        views,
        excluded=excluded,
        profile=profile_for(session, consultant.organisation_id, consultant.key),
        snap_mm=float(settings().get("snap_mm", 0.6)),
    )
    maps = calibration_maps.load()
    calibration_maps.calibrate(found, maps)
    _replace(session, sheet, found, maps.version)
    queue_vision(session, store, sheet, table, placed, excluded)
    log.info(
        "sheet_detected",
        sheet_id=str(sheet.id),
        objects=len(found.objects),
        runs=len(found.runs),
        unconfirmed=skipped,
        pipe=found.pipe_key,
    )
    return SheetOutcome(sheet.id, len(found.objects), len(found.runs), skipped)


def _current_revision(session: Session, sheet_id: uuid.UUID) -> uuid.UUID | None:
    return (
        session.execute(
            select(SheetRevision.id)
            .where(SheetRevision.sheet_id == sheet_id)
            .order_by(SheetRevision.created_at.desc())
        )
        .scalars()
        .first()
    )


def _rejected_places(session: Session, sheet: Sheet) -> tuple[set[Any], list[list[list[float]]]]:
    """What a person said is not there on this sheet (P1-08), by what and where it is."""
    objects = set()
    for row in session.execute(
        select(DetectedObject).where(
            DetectedObject.bid_id == sheet.bid_id,
            DetectedObject.sheet_id == sheet.id,
            DetectedObject.state == "rejected",
        )
    ).scalars():
        position = dict(row.geometry_ref or {})
        objects.add((row.kind, row.object_type, _near(position.get("x")), _near(position.get("y"))))
    runs = [
        [list(p) for p in row.points]
        for row in session.execute(
            select(PipeRun).where(PipeRun.sheet_id == sheet.id, PipeRun.state == "rejected")
        ).scalars()
    ]
    return objects, runs


def _near(value: Any) -> float:
    """A position to the nearest half millimetre: the same symbol, found again."""
    return round(float(value or 0.0) * 2) / 2


def _same_run(points: list[list[float]], rejected: list[list[list[float]]]) -> bool:
    return any(
        len(points) == len(other)
        and all(
            abs(a[0] - b[0]) <= 0.5 and abs(a[1] - b[1]) <= 0.5
            for a, b in zip(points, other, strict=True)
        )
        for other in rejected
    )


def _replace(session: Session, sheet: Sheet, found: SheetDetections, version: str) -> None:
    # A person's "not there" survives detecting the sheet again: the new row for the same
    # symbol or run is rejected too.
    rejected_objects, rejected_runs = _rejected_places(session, sheet)
    # Vision detections are kept: they are the model's work, and asking again is not free.
    session.execute(
        delete(DetectedObject).where(
            DetectedObject.bid_id == sheet.bid_id,
            DetectedObject.sheet_id == sheet.id,
            DetectedObject.extraction_method != "vision",
        )
    )
    session.execute(delete(PipeRun).where(PipeRun.sheet_id == sheet.id))
    revision = _current_revision(session, sheet.id)
    for item in found.objects:
        has_grid = item.view is not None and item.view.grid is not None
        session.add(
            DetectedObject(
                bid_id=sheet.bid_id,
                sheet_id=sheet.id,
                sheet_revision_id=revision,
                kind=item.kind,
                object_type=item.object_type,
                attributes=item.attributes,
                geometry_ref={
                    "x": round(item.x, 3),
                    "y": round(item.y, 3),
                    "box": [round(v, 3) for v in item.box],
                },
                source_ref=item.evidence,
                extraction_method=item.method,
                confidence=item.calibrated_confidence,
                raw_confidence=item.raw_confidence,
                features=item.features,
                calibration_version=version,
                detector_version=DETECTOR_VERSION,
                view_id=item.view.id if item.view else None,
                grid_reference=item.grid_reference,
                level=item.level,
                orientation=item.orientation,
                gaps=_gaps(item, has_grid),
                state="rejected"
                if (item.kind, item.object_type, _near(item.x), _near(item.y)) in rejected_objects
                else "proposed",
            )
        )
    for run in found.runs:
        has_grid = run.view is not None and run.view.grid is not None
        session.add(
            PipeRun(
                bid_id=sheet.bid_id,
                sheet_id=sheet.id,
                sheet_revision_id=revision,
                view_id=run.view.id if run.view else None,
                run_index=run.run_id,
                run_class=run.run_class,
                nominal_dn=run.dn,
                size_status=run.size_status,
                size_reason=run.size_reason or None,
                labels=[
                    {
                        "text": label.text,
                        "dn": label.dn,
                        "x": round(label.x, 3),
                        "y": round(label.y, 3),
                        "distance_mm": label.distance,
                        "confidence": label.confidence,
                    }
                    for label in run.labels
                ],
                paper_length_mm=run.paper_length_mm,
                length_mm=run.length_mm,
                points=run.points,
                geometry_rows=run.rows,
                grid_reference=run.grid_reference,
                level=run.level,
                features=run.features,
                raw_confidence=run.raw_confidence,
                confidence=run.calibrated_confidence
                if run.calibrated_confidence is not None
                else run.raw_confidence,
                calibration_version=version,
                detector_version=DETECTOR_VERSION,
                gaps=_gaps(run, has_grid),
                state="rejected"
                if _same_run([list(p) for p in run.points], rejected_runs)
                else "proposed",
            )
        )
    session.flush()


def detect_all(session: Session, store: ObjectStore, sheets: list[Sheet]) -> list[SheetOutcome]:
    records = session.execute(
        select(SheetGeometry).where(SheetGeometry.sheet_id.in_([sheet.id for sheet in sheets]))
    ).scalars()
    return [detect_sheet(session, store, record) for record in records]


def detect_bid(session: Session, store: ObjectStore, bid_id: uuid.UUID) -> list[SheetOutcome]:
    """Every sheet of a bid again: after a mapping is confirmed, changed or rejected."""
    records = session.execute(select(SheetGeometry).where(SheetGeometry.bid_id == bid_id)).scalars()
    return [detect_sheet(session, store, record) for record in records]


# --- Vision assist (optional, off by default: config/detection.yaml) -----------------------


def queue_vision(
    session: Session,
    store: ObjectStore,
    sheet: Sheet,
    table: Any,
    placed: list[Placed],
    excluded: list[tuple[float, float, float, float]],
    user_id: str = "",
) -> int:
    """Queue the model for clusters nearly like a legend entry. Returns how many were queued.

    Only clusters the deterministic matcher did not place, outside its tolerance but within
    `near_factor` times it, and at most `max_per_sheet` of them. The model is asked on the
    ordinary worker (`detection.vision`), with a crop drawn from the geometry.
    """
    import contextlib
    import hashlib

    from firebid.drawings import crops
    from firebid.jobs.enqueue import enqueue
    from firebid.jobs.tasks import classify_with_vision_job
    from firebid.storage.object_store import ObjectExists

    options = settings().get("vision_assist") or {}
    if not options.get("enabled"):
        return 0
    factor = float(options.get("near_factor", 2.0))
    limit = int(options.get("max_per_sheet", 20))
    entries = list(
        session.execute(select(LegendEntry).where(LegendEntry.bid_id == sheet.bid_id)).scalars()
    )
    references = [Signature.from_json(entry.signature) for entry in entries]
    taken = {(round(p.cx, 2), round(p.cy, 2)) for p in placed}
    queued = 0
    for cluster in symbols.clusters(table, excluding=excluded):
        if queued >= limit:
            break
        centre = (round(cluster.centre[0], 2), round(cluster.centre[1], 2))
        if cluster.signature is None or centre in taken:
            continue
        near = symbols.near_match(cluster.signature, references, factor)
        if near is None:
            continue
        image = crops.render(table, cluster.box, margin_mm=3.0)
        key = f"detections/vision/{hashlib.sha256(image).hexdigest()}.png"
        with contextlib.suppress(ObjectExists):
            store.put_once(key, image, content_type="image/png")
        enqueue(
            session,
            classify_with_vision_job,
            sheet_id=str(sheet.id),
            entry_id=str(entries[near.index].id),
            box=[round(v, 3) for v in cluster.box],
            rows=list(cluster.rows),
            crop_key=key,
            distance=round(near.distance, 4),
            user_id=user_id,
        )
        queued += 1
    return queued


def classify_with_vision(
    session: Session,
    store: ObjectStore,
    router: Any,
    *,
    sheet_id: uuid.UUID,
    entry_id: uuid.UUID,
    box: list[float],
    rows: list[int],
    crop_key: str,
    distance: float,
) -> DetectedObject | None:
    """Ask the model what a nearly-matching symbol is; store it as a capped proposal."""
    from firebid.agents.base import AgentInput
    from firebid.agents.runtime import Escalated, run_agent_with_result
    from firebid.agents.symbol_mapper import LegendRowInput, SymbolMapper, SymbolProposal
    from firebid.drawings.detection import _where

    sheet = session.get(Sheet, sheet_id)
    entry = session.get(LegendEntry, entry_id)
    if sheet is None or entry is None:
        return None
    consultant = symbol_service.consultant_of(session, sheet.bid_id)
    kinds = {kind.key: kind for kind in object_library.usable(session, consultant.organisation_id)}
    image = store.get(crop_key)
    description = (
        "A symbol found on a plan, not in its legend. It resembles the legend entry "
        + repr(entry.description)
        + " but does not match it exactly."
    )
    request = AgentInput(
        bid_id=sheet.bid_id,
        idempotency_key=f"vision:{sheet.id}:{crop_key.rsplit('/', 1)[-1]}",
        payload=LegendRowInput(
            description=description,
            image_png=image,
            choices=[(kind.key, kind.label) for kind in kinds.values()],
            consultant=consultant.name,
        ),
    )
    try:
        run, result = run_agent_with_result(session, SymbolMapper(router), request)
    except Escalated:
        return None  # a person reviews the escalation; nothing is detected
    if result is None or not isinstance(result.output, SymbolProposal):
        return None  # asked before: its detection, if any, is already stored
    answer = result.output
    kind = kinds.get(answer.object_type or "")
    if kind is None or kind.measure != "count":
        return None
    cap = float((settings().get("vision_assist") or {}).get("confidence_cap", 0.5))
    views = _views(session, sheet.id)
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    view, grid_reference = _where(views, cx, cy)
    gaps: dict[str, str] = {}
    if view is None:
        gaps["view"] = "outside every view on the sheet"
    if grid_reference is None:
        gaps["grid_reference"] = (
            "outside the structural grid"
            if view is not None and view.grid is not None
            else "the view shows no structural grid"
        )
    detection = DetectedObject(
        bid_id=sheet.bid_id,
        sheet_id=sheet.id,
        sheet_revision_id=_current_revision(session, sheet.id),
        kind="object",
        object_type=kind.key,
        attributes=dict(answer.attributes),
        geometry_ref={"x": round(cx, 3), "y": round(cy, 3), "box": box},
        source_ref={
            "geometry_rows": rows,
            "crop_key": crop_key,
            "resembles_legend_entry": str(entry.id),
            "agent_run_id": str(run.id),
            "model": run.model,
            "prompt_version": run.prompt_version,
        },
        extraction_method="vision",
        confidence=round(min(answer.confidence, cap), 4),
        raw_confidence=round(answer.confidence, 4),
        features={
            "match_distance": distance,
            "vision_confidence": answer.confidence,
            "reason": answer.reason,
        },
        calibration_version=f"vision-cap-{cap}",
        detector_version=DETECTOR_VERSION,
        view_id=view.id if view else None,
        grid_reference=grid_reference,
        level=view.level if view else None,
        gaps=gaps,
        state="proposed",
    )
    session.add(detection)
    session.flush()
    return detection
