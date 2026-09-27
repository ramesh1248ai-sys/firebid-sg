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


def _replace(session: Session, sheet: Sheet, found: SheetDetections, version: str) -> None:
    session.execute(
        delete(DetectedObject).where(
            DetectedObject.bid_id == sheet.bid_id, DetectedObject.sheet_id == sheet.id
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
                state="proposed",
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
                state="proposed",
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
