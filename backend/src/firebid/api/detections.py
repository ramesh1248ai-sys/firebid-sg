"""A bid's detections: every object, riser, drop and pipe run, as proposals (P1-05).

Read by the verification workbench (P1-08), which is where a person accepts or corrects
them; nothing is accepted here. Ordered for review: least confident first, so what most
needs a person is at the top (FR-VIS-09).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from firebid.api.deps import CurrentBid, DbSession, require
from firebid.auth.permissions import Action
from firebid.auth.provisioning import Principal
from firebid.db.models.takeoff import DetectedObject, PipeRun

router = APIRouter(prefix="/bids/{bid_id}/detections", tags=["detections"])


class DetectionOut(BaseModel):
    id: uuid.UUID
    sheet_id: uuid.UUID
    kind: str
    object_type: str
    attributes: dict[str, Any]
    position: dict[str, Any] | None
    method: str
    evidence: dict[str, Any]
    view_id: uuid.UUID | None
    grid_reference: str | None
    level: str | None
    orientation: float | None
    confidence: float | None
    raw_confidence: float | None
    calibration_version: str | None
    gaps: dict[str, str]
    state: str


class PipeRunOut(BaseModel):
    id: uuid.UUID
    sheet_id: uuid.UUID
    run_class: str
    nominal_dn: int | None
    size_status: str
    size_reason: str | None
    labels: list[dict[str, Any]]
    paper_length_mm: float
    length_mm: float | None
    points: list[list[float]]
    view_id: uuid.UUID | None
    grid_reference: str | None
    level: str | None
    confidence: float
    gaps: dict[str, str]
    state: str


class DetectionsOut(BaseModel):
    objects: list[DetectionOut]
    runs: list[PipeRunOut]


@router.get("", response_model=DetectionsOut)
def list_detections(
    context: CurrentBid,
    session: DbSession,
    sheet_id: Annotated[uuid.UUID | None, Query()] = None,
) -> DetectionsOut:
    objects = select(DetectedObject).where(DetectedObject.bid_id == context.bid.id)
    runs = select(PipeRun).where(PipeRun.bid_id == context.bid.id)
    if sheet_id is not None:
        objects = objects.where(DetectedObject.sheet_id == sheet_id)
        runs = runs.where(PipeRun.sheet_id == sheet_id)
    return DetectionsOut(
        objects=[
            DetectionOut(
                id=row.id,
                sheet_id=row.sheet_id,
                kind=row.kind,
                object_type=row.object_type,
                attributes=dict(row.attributes or {}),
                position=row.geometry_ref,
                method=row.extraction_method,
                evidence=dict(row.source_ref or {}),
                view_id=row.view_id,
                grid_reference=row.grid_reference,
                level=row.level,
                orientation=row.orientation,
                confidence=row.confidence,
                raw_confidence=row.raw_confidence,
                calibration_version=row.calibration_version,
                gaps=dict(row.gaps or {}),
                state=row.state,
            )
            for row in session.execute(
                objects.order_by(DetectedObject.confidence.asc().nulls_first())
            ).scalars()
        ],
        runs=[
            PipeRunOut(
                id=row.id,
                sheet_id=row.sheet_id,
                run_class=row.run_class,
                nominal_dn=row.nominal_dn,
                size_status=row.size_status,
                size_reason=row.size_reason,
                labels=list(row.labels or []),
                paper_length_mm=row.paper_length_mm,
                length_mm=row.length_mm,
                points=row.points,
                view_id=row.view_id,
                grid_reference=row.grid_reference,
                level=row.level,
                confidence=row.confidence,
                gaps=dict(row.gaps or {}),
                state=row.state,
            )
            for row in session.execute(runs.order_by(PipeRun.confidence.asc())).scalars()
        ],
    )


@router.post("/run", status_code=202)
def run_detection_again(
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.DOCUMENT_REVIEW)],
) -> dict[str, str]:
    """Queue detection of every sheet again, as confirming a mapping does."""
    from firebid.jobs.enqueue import enqueue
    from firebid.jobs.tasks import run_detection

    enqueue(session, run_detection, bid_id=str(context.bid.id), user_id=str(principal.user_id))
    return {"status": "queued"}
