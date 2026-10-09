"""Labour estimation (FR-LAB-01, 02, 03; P2-05).

* `GET /labour/productivity`, `POST /labour/productivity` (one entry),
  `POST /labour/productivity/import` (a workbook), `GET /labour/productivity/{id}/history`.
* `GET /labour/catalogue`: the multipliers and the labour rate build-up in force on a day.
* `GET /bids/{id}/labour`: hours and cost per BOQ line, per system and by trade, with every
  factor; `POST .../labour/conditions/propose`; `PUT .../labour/conditions`.

Every factor of a labour figure is shown with where it comes from: the library entry's
source, each multiplier's source and rationale and who confirmed it, and the rate table's
effective date.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from firebid.api.deps import CurrentBid, CurrentPrincipal, DbSession, require
from firebid.auth.permissions import Action
from firebid.auth.provisioning import Principal
from firebid.db.models.core import Bid
from firebid.db.models.labour import LabourCondition, LabourProductivity
from firebid.labour import multipliers, rates
from firebid.labour.productivity import SOURCE_LABELS
from firebid.services import labour

company_router = APIRouter(prefix="/labour", tags=["labour"])
router = APIRouter(prefix="/bids/{bid_id}/labour", tags=["labour"])

MAX_LIST_BYTES = 10 * 1024 * 1024


def refused(error: Exception, code: int = status.HTTP_409_CONFLICT) -> HTTPException:
    return HTTPException(code, str(error))


# --- The productivity library -----------------------------------------------------------------


class EntryOut(BaseModel):
    id: uuid.UUID
    item_type: str
    dn: str
    joining: str
    description: str
    unit: str
    hours_per_unit: Decimal
    trade: str
    source_type: str
    source_reference: str
    source: str
    version: int
    retired_at: datetime | None
    created_at: datetime


class EntryIn(BaseModel):
    item_type: str = Field(min_length=1, max_length=80)
    dn: str = Field(default="", max_length=40)
    joining: str = Field(default="", max_length=60)
    description: str = Field(min_length=1, max_length=500)
    unit: str = Field(min_length=1, max_length=16)
    hours_per_unit: Decimal = Field(gt=0)
    trade: str = Field(min_length=1, max_length=40)
    source_type: str = Field(pattern="^(company_standard|historical_project|estimator_judgement)$")
    source_reference: str | None = Field(default=None, max_length=200)


class ProductivityImportOut(BaseModel):
    imported: bool
    sheet: str | None
    created: int
    superseded: int
    unchanged: int
    problems: list[dict[str, Any]]


def entry_out(row: LabourProductivity) -> EntryOut:
    return EntryOut(
        id=row.id,
        item_type=row.item_type,
        dn=row.dn,
        joining=row.joining,
        description=row.description,
        unit=row.unit,
        hours_per_unit=row.hours_per_unit,
        trade=row.trade,
        source_type=row.source_type,
        source_reference=row.source_reference,
        source=f"{SOURCE_LABELS.get(row.source_type, row.source_type)}: {row.source_reference}",
        version=row.version,
        retired_at=row.retired_at,
        created_at=row.created_at,
    )


@company_router.get("/productivity", response_model=list[EntryOut])
def list_productivity(principal: CurrentPrincipal, session: DbSession) -> list[EntryOut]:
    """The library's current entries, each with its source."""
    return [entry_out(row) for row in labour.current_entries(session, principal.organisation_id)]


@company_router.get("/productivity/{entry_id}/history", response_model=list[EntryOut])
def productivity_history(
    entry_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession
) -> list[EntryOut]:
    row = session.get(LabourProductivity, entry_id)
    if row is None or row.organisation_id != principal.organisation_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such productivity entry")
    return [entry_out(version) for version in labour.history(session, row)]


@company_router.post("/productivity", response_model=EntryOut, status_code=201)
def set_productivity(
    body: EntryIn,
    session: DbSession,
    principal: Annotated[Principal, require(Action.LABOUR_PRODUCTIVITY_ADJUST)],
) -> EntryOut:
    """One entry. An estimator's judgement with no reference given is under their name."""
    try:
        row = labour.set_entry(
            session,
            principal.organisation_id,
            principal.actor(),
            item_type=body.item_type,
            dn=body.dn,
            joining=body.joining,
            description=body.description,
            unit=body.unit,
            hours=body.hours_per_unit,
            trade=body.trade,
            source_type=body.source_type,
            source_reference=body.source_reference,
        )
    except labour.LabourError as refusal:
        raise refused(refusal, status.HTTP_422_UNPROCESSABLE_CONTENT) from refusal
    return entry_out(row)


@company_router.post("/productivity/import", response_model=ProductivityImportOut)
async def import_productivity(
    file: Annotated[UploadFile, File()],
    session: DbSession,
    principal: Annotated[Principal, require(Action.LABOUR_PRODUCTIVITY_ADJUST)],
) -> ProductivityImportOut:
    """A productivity list workbook, all or nothing: any problem imports nothing."""
    payload = await file.read(MAX_LIST_BYTES + 1)
    if len(payload) > MAX_LIST_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "a list is at most 10 MB")
    if not payload.startswith(b"PK"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "a productivity list is an .xlsx workbook"
        )
    try:
        report = labour.import_productivity(
            session,
            principal.organisation_id,
            payload,
            principal.actor(),
            file.filename or "productivity.xlsx",
        )
    except labour.LabourError as refusal:
        raise refused(refusal, status.HTTP_422_UNPROCESSABLE_CONTENT) from refusal
    return ProductivityImportOut(
        imported=report.imported,
        sheet=report.sheet,
        created=report.created,
        superseded=report.superseded,
        unchanged=report.unchanged,
        problems=report.problems,
    )


# --- The catalogue: multipliers and the rate build-up -----------------------------------------


class MultiplierOut(BaseModel):
    key: str
    label: str
    kind: str
    value: Decimal
    source: str
    rationale: str
    up_to_mm: int | None


class ComponentOut(BaseModel):
    key: str
    label: str
    hourly: Decimal
    basis: str


class GradeRateOut(BaseModel):
    grade: str
    label: str
    components: list[ComponentOut]
    hourly: Decimal


class CrewOut(BaseModel):
    grade: str
    percent: Decimal


class TradeRateOut(BaseModel):
    trade: str
    label: str
    crew: list[CrewOut]
    grades: list[GradeRateOut]
    hourly: Decimal
    effective_from: date
    source: str


class CatalogueOut(BaseModel):
    on: date
    multipliers: list[MultiplierOut]
    # The table in force on the day, and every table's effective date.
    rate_table_effective_from: date | None
    rate_table_source: str | None
    rate_tables: list[date]
    trades: list[TradeRateOut]


def _trades(day: date) -> list[TradeRateOut]:
    try:
        found = rates.trades_on(day)
    except ValueError:
        return []
    return [TradeRateOut(**trade.as_json()) for trade in found]


def catalogue_out(day: date) -> CatalogueOut:
    trades = _trades(day)
    return CatalogueOut(
        on=day,
        multipliers=[MultiplierOut(**item.as_json()) for item in multipliers.catalogue()],
        rate_table_effective_from=trades[0].effective_from if trades else None,
        rate_table_source=trades[0].source if trades else None,
        rate_tables=[table.effective_from for table in rates.tables()],
        trades=trades,
    )


@company_router.get("/catalogue", response_model=CatalogueOut)
def catalogue(principal: CurrentPrincipal, on: date | None = None) -> CatalogueOut:
    """The multipliers, each with its source and rationale, and each trade's hourly rate
    built up line by line from the rate table in force on the day (today by default)."""
    return catalogue_out(on or datetime.now(UTC).date())


# --- A bid's labour ---------------------------------------------------------------------------


class AppliedOut(BaseModel):
    key: str
    label: str
    value: Decimal
    source: str
    rationale: str
    scope: str
    confirmed_by: str
    basis: str


class PortionOut(BaseModel):
    """The part of a line on one level: a line billed for the building is worked level by
    level, and each level carries its own multipliers."""

    level: str | None
    quantity: Decimal
    baseline_hours: Decimal
    multipliers: list[str]
    factor: Decimal
    hours: Decimal


class LabourLineOut(BaseModel):
    line_id: uuid.UUID
    reference: str
    description: str
    section: str
    level: str | None
    unit: str
    quantity: Decimal
    hours_per_unit: Decimal | None
    productivity_source: str | None
    productivity_entry_id: uuid.UUID | None
    trade: str | None
    baseline_hours: Decimal | None
    multipliers: list[AppliedOut]
    factor: Decimal
    hours: Decimal | None
    hourly_rate: Decimal | None
    cost: Decimal | None
    reason: str
    # Empty for a line that is of one place: its own level, or no level at all.
    by_level: list[PortionOut] = []


class SubtotalOut(BaseModel):
    key: str
    label: str
    baseline_hours: Decimal
    hours: Decimal
    cost: Decimal
    hourly_rate: Decimal | None


class ConditionOut(BaseModel):
    id: uuid.UUID
    level: str | None
    multiplier_key: str
    label: str
    value: Decimal | None
    source: str | None
    rationale: str | None
    state: str
    basis: str
    proposed_by: str
    decided_by: str | None
    decided_at: datetime | None


class LabourOut(BaseModel):
    priced_on: date
    lines: list[LabourLineOut]
    by_section: list[SubtotalOut]
    by_trade: list[SubtotalOut]
    baseline_hours: Decimal
    hours: Decimal
    cost: Decimal
    without_hours: int
    conditions: list[ConditionOut]
    levels: list[str]
    catalogue: CatalogueOut


class ConditionIn(BaseModel):
    key: str = Field(min_length=1, max_length=60)
    level: str | None = Field(default=None, max_length=40)
    state: str = Field(pattern="^(confirmed|rejected)$")
    basis: str | None = Field(default=None, max_length=2000)


def condition_out(row: LabourCondition) -> ConditionOut:
    item = multipliers.by_key().get(row.multiplier_key)
    return ConditionOut(
        id=row.id,
        level=row.level,
        multiplier_key=row.multiplier_key,
        label=item.label if item else row.multiplier_key,
        value=item.value if item else None,
        source=item.source if item else None,
        rationale=item.rationale if item else None,
        state=row.state,
        basis=row.basis,
        proposed_by=row.proposed_by,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
    )


def labour_out(session: DbSession, bid: Bid) -> LabourOut:
    found = labour.estimate(session, bid)
    day = labour.pricing_day(session, bid)
    return LabourOut(
        priced_on=day,
        lines=[LabourLineOut(**line.as_json()) for line in found.lines],
        by_section=[SubtotalOut(**total.as_json()) for total in found.by_section],
        by_trade=[SubtotalOut(**total.as_json()) for total in found.by_trade],
        baseline_hours=found.baseline_hours,
        hours=found.hours,
        cost=found.cost.amount,
        without_hours=found.without_hours,
        conditions=[condition_out(row) for row in labour.conditions(session, bid.id)],
        levels=sorted({line.line.level for line in found.lines if line.line.level}),
        catalogue=catalogue_out(day),
    )


@router.get("", response_model=LabourOut)
def get_labour(context: CurrentBid, session: DbSession) -> LabourOut:
    """Labour hours and cost for each line of the bill, per system and by trade: baseline
    hours, each multiplier applied, and the rate, each with its source."""
    return labour_out(session, context.bid)


@router.post("/conditions/propose", response_model=LabourOut)
def propose_conditions(
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.BOQ_EDIT)],
) -> LabourOut:
    """Propose multipliers from the bid's parameters. A proposal changes no hours until an
    estimator confirms it."""
    labour.propose(session, context.bid, principal.actor())
    return labour_out(session, context.bid)


@router.put("/conditions", response_model=LabourOut)
def decide_condition(
    body: ConditionIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.BOQ_EDIT)],
) -> LabourOut:
    """Confirm or reject a multiplier for the bid or one of its levels."""
    try:
        labour.decide(
            session,
            context.bid,
            principal.actor(),
            key=body.key,
            level=body.level,
            state=body.state,
            basis=body.basis,
        )
    except labour.LabourError as refusal:
        raise refused(refusal, status.HTTP_422_UNPROCESSABLE_CONTENT) from refusal
    return labour_out(session, context.bid)
