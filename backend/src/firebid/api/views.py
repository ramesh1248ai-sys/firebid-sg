"""A sheet's views: their scales, measuring on them, calibrating them, and locating points.

Measurement is refused, with a 409 that says why, on any view whose scale is not verified or
calibrated (FR-VIS-05). Calibration is a person's act and needs the document-review role.
Every route resolves the sheet through `CurrentBid`, so another bid's sheet is a 404.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from firebid.api.deps import CurrentBid, DbSession, require
from firebid.auth.permissions import Action
from firebid.auth.provisioning import Principal
from firebid.db.models.documents import Sheet
from firebid.db.models.drawings import SheetView
from firebid.drawings.scale import NotMeasurable
from firebid.services import views as view_service

router = APIRouter(prefix="/bids/{bid_id}/sheets/{sheet_id}", tags=["views"])

Point = Annotated[list[float], Field(min_length=2, max_length=2)]


class ViewOut(BaseModel):
    id: uuid.UUID
    sheet_id: uuid.UUID
    ordinal: int
    kind: str
    title: str | None
    source: str
    extent: list[float]
    level: str | None
    stated_scale: str | None
    stated_denominator: float | None
    scale_status: str
    denominator: float | None
    measurable: bool = False
    scale_evidence: dict[str, object]
    grid: dict[str, object] | None
    grid_box: list[float] | None
    calibration: dict[str, object] | None
    calibrated_by: str | None

    model_config = {"from_attributes": True}


class CalibrateRequest(BaseModel):
    points: Annotated[list[Point], Field(min_length=2, max_length=2)]
    distance_mm: float = Field(gt=0)


class MeasureRequest(BaseModel):
    points: Annotated[list[Point], Field(min_length=2, max_length=10_000)]


class MeasureOut(BaseModel):
    length_mm: float
    denominator: float
    scale_status: str


class LocationOut(BaseModel):
    view_id: uuid.UUID | None
    view_kind: str | None
    grid_reference: str | None
    grid_index: list[float] | None
    level: str | None
    zone: str | None


def _sheet(session: DbSession, context: CurrentBid, sheet_id: uuid.UUID) -> Sheet:
    sheet = session.get(Sheet, sheet_id)
    if sheet is None or sheet.bid_id != context.bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
    return sheet


def _view(session: DbSession, sheet: Sheet, view_id: uuid.UUID) -> SheetView:
    view = session.get(SheetView, view_id)
    if view is None or view.sheet_id != sheet.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "view not found")
    return view


def _out(view: SheetView) -> ViewOut:
    out = ViewOut.model_validate(view)
    out.measurable = view_service.verdict_of(view).measurable
    return out


@router.get("/views", response_model=list[ViewOut])
def list_views(context: CurrentBid, session: DbSession, sheet_id: uuid.UUID) -> list[ViewOut]:
    sheet = _sheet(session, context, sheet_id)
    rows = session.execute(
        select(SheetView).where(SheetView.sheet_id == sheet.id).order_by(SheetView.ordinal)
    ).scalars()
    return [_out(view) for view in rows]


@router.post("/views/{view_id}/calibrate", response_model=ViewOut)
def calibrate_view(
    sheet_id: uuid.UUID,
    view_id: uuid.UUID,
    body: CalibrateRequest,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.DOCUMENT_REVIEW)],
) -> ViewOut:
    """Two sheet points and the real distance between them: the view becomes measurable."""
    view = _view(session, _sheet(session, context, sheet_id), view_id)
    (x0, y0), (x1, y1) = body.points
    try:
        view_service.calibrate(
            session, view, ((x0, y0), (x1, y1)), body.distance_mm, principal.actor()
        )
    except ValueError as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal
    return _out(view)


@router.post("/views/{view_id}/measure", response_model=MeasureOut)
def measure_on_view(
    sheet_id: uuid.UUID,
    view_id: uuid.UUID,
    body: MeasureRequest,
    context: CurrentBid,
    session: DbSession,
) -> MeasureOut:
    """A length along sheet points, in millimetres. 409 unless the scale is measurable."""
    view = _view(session, _sheet(session, context, sheet_id), view_id)
    try:
        length = view_service.measure(view, [(x, y) for x, y in body.points])
    except NotMeasurable as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal
    return MeasureOut(
        length_mm=round(length, 1),
        denominator=float(view.denominator or 0),
        scale_status=view.scale_status,
    )


@router.get("/locate", response_model=LocationOut)
def locate_point(
    context: CurrentBid,
    session: DbSession,
    sheet_id: uuid.UUID,
    x: Annotated[float, Query(description="sheet millimetres from the left")],
    y: Annotated[float, Query(description="sheet millimetres from the top")],
) -> LocationOut:
    """A sheet point as an estimator says it: grid reference, level and zone."""
    found = view_service.locate(session, _sheet(session, context, sheet_id), x, y)
    return LocationOut(
        view_id=found.view.id if found.view else None,
        view_kind=found.view.kind if found.view else None,
        grid_reference=found.grid_reference,
        grid_index=[round(value, 4) for value in found.grid_index] if found.grid_index else None,
        level=found.level,
        zone=found.zone,
    )
