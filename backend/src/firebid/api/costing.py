"""Supplier quotations and the cost build-up (FR-CST-02 to 09; P2-04).

* `POST /bids/{id}/quotations` (a file), `GET .../quotations`, `GET .../quotations/{id}`.
* `POST .../quotations/{id}/fields`, `.../lines/{line}/link`, `.../read-with-model`,
  `.../confirm`, `.../reject`.
* `GET /bids/{id}/cost/build-up`; `PUT`, `DELETE .../cost/build-up/{component}`;
  `PUT .../cost/priced-on`; `GET .../cost/history`; `PUT .../cost/allowances/{line}`.
* `GET`, `POST /costing/fx-rates`; `POST /costing/erp/import`.

The platform never makes a price: a quotation line's price is the supplier's, a build-up
figure is an estimator's, and each says whose it is.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from firebid.api.deps import CurrentBid, CurrentPrincipal, DbSession, require
from firebid.auth.permissions import Action
from firebid.auth.provisioning import Principal
from firebid.db.models.commercial import BoqLine
from firebid.db.models.core import Bid
from firebid.db.models.costing import FxRate, Quotation, QuotationLine
from firebid.ingest.scanning import get_scanner
from firebid.pricing import buildup
from firebid.pricing import landed as landing
from firebid.pricing.keys import ItemKey
from firebid.services import boq, costing
from firebid.services import quotations as service
from firebid.storage.object_store import get_object_store

router = APIRouter(prefix="/bids/{bid_id}", tags=["costing"])
company_router = APIRouter(prefix="/costing", tags=["costing"])

MAX_QUOTATION_BYTES = 25 * 1024 * 1024
MAX_ERP_BYTES = 25 * 1024 * 1024


def refused(error: Exception, code: int = status.HTTP_409_CONFLICT) -> HTTPException:
    return HTTPException(code, str(error))


# --- Quotations -------------------------------------------------------------------------------


class FlagOut(BaseModel):
    code: str
    message: str


class QuotationLineOut(BaseModel):
    id: uuid.UUID
    ordinal: int
    description: str
    brand: str | None
    model: str | None
    unit: str | None
    unit_price: Decimal
    moq: str | None
    lead_time: str | None
    source: dict[str, Any]
    item_key: str | None
    item_label: str | None
    boq_line_id: uuid.UUID | None
    rate_id: uuid.UUID | None
    landed: dict[str, Any] | None


class QuotationOut(BaseModel):
    id: uuid.UUID
    filename: str
    kind: str
    state: str
    supplier: str | None
    quote_number: str | None
    quote_date: date | None
    valid_until: date | None
    currency: str | None
    delivery_terms: str | None
    incoterm: str | None
    lead_time: str | None
    exclusions: list[str]
    # Each field as read, with the line of the file it was read from, and how.
    fields: dict[str, Any]
    missing: list[str]
    method: str
    model: dict[str, Any] | None
    flags: list[FlagOut]
    decided_by: str | None
    decided_at: datetime | None
    note: str | None
    lines: list[QuotationLineOut]


class QuotationDetailOut(QuotationOut):
    # The file's own lines, for the confirmation view to show beside the fields.
    source_lines: list[dict[str, Any]]


class FieldsIn(BaseModel):
    supplier: str | None = Field(default=None, max_length=200)
    quote_number: str | None = Field(default=None, max_length=120)
    quote_date: date | None = None
    valid_until: date | None = None
    currency: str | None = Field(default=None, max_length=3)
    delivery_terms: str | None = Field(default=None, max_length=200)
    incoterm: str | None = Field(default=None, max_length=3)
    lead_time: str | None = Field(default=None, max_length=120)
    exclusions: list[str] | None = None


class LinkIn(BaseModel):
    item_key: str | None = Field(default=None, max_length=200)
    boq_line_id: uuid.UUID | None = None
    unit: str | None = Field(default=None, max_length=16)
    unit_price: Decimal | None = None


class RejectIn(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


def _line_out(line: QuotationLine) -> QuotationLineOut:
    return QuotationLineOut(
        id=line.id,
        ordinal=line.ordinal,
        description=line.description,
        brand=line.brand,
        model=line.model,
        unit=line.unit,
        unit_price=line.unit_price,
        moq=line.moq,
        lead_time=line.lead_time,
        source=dict(line.source or {}),
        item_key=line.item_key,
        item_label=ItemKey.parse(line.item_key).label() if line.item_key else None,
        boq_line_id=line.boq_line_id,
        rate_id=line.rate_id,
        landed=dict(line.landed) if line.landed else None,
    )


def _values(session: DbSession, bid: Bid, row: Quotation) -> dict[str, Any]:
    extraction = dict(row.extraction or {})
    return {
        "id": row.id,
        "filename": row.filename,
        "kind": row.kind,
        "state": row.state,
        "supplier": row.supplier,
        "quote_number": row.quote_number,
        "quote_date": row.quote_date,
        "valid_until": row.valid_until,
        "currency": row.currency,
        "delivery_terms": row.delivery_terms,
        "incoterm": row.incoterm,
        "lead_time": row.lead_time,
        "exclusions": list(row.exclusions or []),
        "fields": dict(extraction.get("fields") or {}),
        "missing": list(extraction.get("missing") or []),
        "method": str(extraction.get("method") or "rules"),
        "model": extraction.get("model"),
        "flags": [FlagOut(**flag.as_json()) for flag in service.flags_of(bid, row)],
        "decided_by": row.decided_by,
        "decided_at": row.decided_at,
        "note": row.note,
        "lines": [_line_out(line) for line in service.lines_of(session, row)],
    }


def quotation_out(session: DbSession, bid: Bid, row: Quotation) -> QuotationOut:
    return QuotationOut(**_values(session, bid, row))


def detail_out(session: DbSession, bid: Bid, row: Quotation) -> QuotationDetailOut:
    source = list((row.extraction or {}).get("source_lines") or [])
    return QuotationDetailOut(**_values(session, bid, row), source_lines=source)


def _quotation(session: DbSession, bid: Bid, quotation_id: uuid.UUID) -> Quotation:
    row = session.get(Quotation, quotation_id)
    if row is None or row.bid_id != bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such quotation")
    return row


@router.post("/quotations", response_model=QuotationDetailOut, status_code=201)
async def upload_quotation(
    file: Annotated[UploadFile, File()],
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.BOQ_EDIT)],
) -> QuotationDetailOut:
    """A supplier's quotation: a PDF, a workbook or an .eml email with its attachments. It
    is scanned, then read in the sandbox, and what was read waits for a person."""
    payload = await file.read(MAX_QUOTATION_BYTES + 1)
    if len(payload) > MAX_QUOTATION_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "a quotation is at most 25 MB"
        )
    try:
        row = service.capture(
            session,
            context.bid,
            payload,
            file.filename or "quotation",
            principal.actor(),
            store=get_object_store(),
            scanner=get_scanner(),
        )
    except service.QuotationError as refusal:
        raise refused(refusal, status.HTTP_422_UNPROCESSABLE_CONTENT) from refusal
    return detail_out(session, context.bid, row)


@router.get("/quotations", response_model=list[QuotationOut])
def list_quotations(context: CurrentBid, session: DbSession) -> list[QuotationOut]:
    """The bid's quotations, each with its flags: expired, shorter than the tender's
    validity, or with exclusions."""
    return [
        quotation_out(session, context.bid, row)
        for row in service.quotations(session, context.bid.id)
    ]


@router.get("/quotations/{quotation_id}", response_model=QuotationDetailOut)
def get_quotation(
    quotation_id: uuid.UUID, context: CurrentBid, session: DbSession
) -> QuotationDetailOut:
    """A quotation as read, each field beside the line of the file it was read from."""
    return detail_out(session, context.bid, _quotation(session, context.bid, quotation_id))


@router.post("/quotations/{quotation_id}/fields", response_model=QuotationDetailOut)
def correct_quotation(
    quotation_id: uuid.UUID,
    body: FieldsIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.BOQ_EDIT)],
) -> QuotationDetailOut:
    row = _quotation(session, context.bid, quotation_id)
    try:
        service.correct(
            session, context.bid, row, body.model_dump(exclude_unset=True), principal.actor()
        )
    except service.QuotationError as refusal:
        raise refused(refusal) from refusal
    return detail_out(session, context.bid, row)


@router.post("/quotations/{quotation_id}/lines/{line_id}/link", response_model=QuotationDetailOut)
def link_quotation_line(
    quotation_id: uuid.UUID,
    line_id: uuid.UUID,
    body: LinkIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.BOQ_EDIT)],
) -> QuotationDetailOut:
    """Say which rate-library item key or BOQ line a quotation line prices."""
    row = _quotation(session, context.bid, quotation_id)
    line = session.get(QuotationLine, line_id)
    if line is None or line.quotation_id != row.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such quotation line")
    try:
        service.link_line(
            session,
            context.bid,
            line,
            principal.actor(),
            item_key=body.item_key,
            boq_line_id=body.boq_line_id,
            unit=body.unit,
            unit_price=body.unit_price,
        )
    except service.QuotationError as refusal:
        raise refused(refusal) from refusal
    return detail_out(session, context.bid, row)


@router.post("/quotations/{quotation_id}/read-with-model", response_model=QuotationDetailOut)
def read_with_model(
    quotation_id: uuid.UUID,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.BOQ_EDIT)],
) -> QuotationDetailOut:
    """Ask the model for what the rules could not read. Its answers are proposals, kept only
    where the line of the file they cite bears them out."""
    from firebid.ai_gateway import gateway

    row = _quotation(session, context.bid, quotation_id)
    try:
        service.read_with_model(session, context.bid, row, gateway(), principal.actor())
    except service.QuotationError as refusal:
        raise refused(refusal) from refusal
    return detail_out(session, context.bid, row)


@router.post("/quotations/{quotation_id}/confirm", response_model=QuotationDetailOut)
def confirm_quotation(
    quotation_id: uuid.UUID,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.SUPPLIER_PRICE_SELECT)],
) -> QuotationDetailOut:
    """Confirm the quotation as read: its linked lines become rate-library entries at their
    landed cost in SGD."""
    row = _quotation(session, context.bid, quotation_id)
    try:
        service.confirm(session, context.bid, row, principal.actor())
    except service.QuotationError as refusal:
        raise refused(refusal) from refusal
    return detail_out(session, context.bid, row)


@router.post("/quotations/{quotation_id}/reject", response_model=QuotationDetailOut)
def reject_quotation(
    quotation_id: uuid.UUID,
    body: RejectIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.SUPPLIER_PRICE_SELECT)],
) -> QuotationDetailOut:
    row = _quotation(session, context.bid, quotation_id)
    try:
        service.reject(session, context.bid, row, principal.actor(), body.note)
    except service.QuotationError as refusal:
        raise refused(refusal) from refusal
    return detail_out(session, context.bid, row)


# --- The build-up -----------------------------------------------------------------------------


class BuildUpLineOut(BaseModel):
    component: str
    label: str
    basis: str
    detail: str
    source: str
    amount: Decimal | None
    entered: bool  # a component an estimator enters, rather than one the bill gives
    bases: list[str]  # what a percentage of it may be of


class BuildUpOut(BaseModel):
    lines: list[BuildUpLineOut]
    direct: Decimal
    cost: Decimal
    cost_with_contingency: Decimal
    total: Decimal  # exclusive of GST
    gst_percent: Decimal
    gst_effective_from: date
    gst: Decimal
    total_with_gst: Decimal
    priced_on: date
    priced_on_set: bool
    unpriced_lines: int
    not_set: list[str]
    base_labels: dict[str, str]


class EnteredIn(BaseModel):
    basis: str = Field(pattern="^(lump_sum|percentage)$")
    amount: Decimal | None = Field(default=None, ge=0)
    percent: Decimal | None = Field(default=None, ge=0, le=100)
    base: str | None = Field(default=None, max_length=32)
    note: str | None = Field(default=None, max_length=2000)


class PricedOnIn(BaseModel):
    priced_on: date


class ComparisonOut(BaseModel):
    line_id: uuid.UUID
    item_no: str | None
    description: str
    item_key: str
    unit: str
    price: Decimal
    history: int
    low: Decimal | None
    high: Decimal | None
    median: Decimal | None
    latest: dict[str, Any] | None
    deviation_percent: Decimal | None
    tolerance_percent: Decimal
    outlier: bool


class AllowanceIn(BaseModel):
    amount: Decimal | None = Field(default=None, ge=0)


class AllowanceOut(BaseModel):
    line_id: uuid.UUID
    amount: Decimal | None
    allowance_by: str | None


def build_up_out(session: DbSession, bid: Bid) -> BuildUpOut:
    found = costing.build_up(session, bid)
    return BuildUpOut(
        lines=[
            BuildUpLineOut(
                component=line.component,
                label=line.label,
                basis=line.basis,
                detail=line.detail,
                source=line.source,
                amount=line.amount.amount if line.amount is not None else None,
                entered=line.component in buildup.ENTERED,
                bases=list(buildup.ALLOWED_BASES.get(line.component, ())),
            )
            for line in found.lines
        ],
        direct=found.direct.amount,
        cost=found.cost.amount,
        cost_with_contingency=found.cost_with_contingency.amount,
        total=found.total.amount,
        gst_percent=found.gst.rate.percent,
        gst_effective_from=found.gst.rate.effective_from,
        gst=found.gst.gst.amount,
        total_with_gst=found.gst.inclusive.amount,
        priced_on=found.priced_on,
        priced_on_set=costing.priced_on(session, bid.id) is not None,
        unpriced_lines=found.unpriced_lines,
        not_set=list(found.not_set),
        base_labels=dict(buildup.BASES),
    )


@router.get("/cost/build-up", response_model=BuildUpOut)
def get_build_up(context: CurrentBid, session: DbSession) -> BuildUpOut:
    """Every cost component as its own line, with its basis and source, and the totals:
    exclusive of GST, the GST, and with it."""
    return build_up_out(session, context.bid)


@router.put("/cost/build-up/{component}", response_model=BuildUpOut)
def enter_component(
    component: str,
    body: EnteredIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.BOQ_EDIT)],
) -> BuildUpOut:
    """An estimator's figure for a component, under their name."""
    try:
        costing.enter(
            session,
            context.bid,
            principal.actor(),
            component=component,
            basis=body.basis,
            amount=body.amount,
            percent=body.percent,
            base=body.base,
            note=body.note,
        )
    except costing.CostingError as refusal:
        raise refused(refusal, status.HTTP_422_UNPROCESSABLE_CONTENT) from refusal
    return build_up_out(session, context.bid)


@router.delete("/cost/build-up/{component}", response_model=BuildUpOut)
def clear_component(
    component: str,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.BOQ_EDIT)],
) -> BuildUpOut:
    try:
        costing.clear(session, context.bid, component, principal.actor())
    except costing.CostingError as refusal:
        raise refused(refusal, status.HTTP_404_NOT_FOUND) from refusal
    return build_up_out(session, context.bid)


@router.put("/cost/priced-on", response_model=BuildUpOut)
def set_priced_on(
    body: PricedOnIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.BOQ_EDIT)],
) -> BuildUpOut:
    """The day the bid is priced, which fixes its GST rate."""
    costing.set_priced_on(session, context.bid, body.priced_on, principal.actor())
    return build_up_out(session, context.bid)


@router.get("/cost/history", response_model=list[ComparisonOut])
def price_history(context: CurrentBid, session: DbSession) -> list[ComparisonOut]:
    """Each priced line beside past purchase order and project prices for its item key,
    flagged when it is beyond the tolerance."""
    return [
        ComparisonOut(
            line_id=found.line_id,
            item_no=found.item_no,
            description=found.description,
            **found.comparison.as_json(),
        )
        for found in costing.comparisons(session, context.bid)
    ]


@router.put("/cost/allowances/{line_id}", response_model=AllowanceOut)
def set_allowance(
    line_id: uuid.UUID,
    body: AllowanceIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.BOQ_EDIT)],
) -> AllowanceOut:
    """An estimator's allowance on a provisional or lump sum line, under their name; with no
    amount, the line is left unpriced."""
    line = session.get(BoqLine, line_id)
    if line is None or line.bid_id != context.bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such BOQ line")
    try:
        boq.set_allowance(session, line, body.amount, principal.actor())
    except boq.BoqError as refusal:
        raise refused(refusal) from refusal
    return AllowanceOut(
        line_id=line.id,
        amount=line.amount.amount if line.amount is not None else None,
        allowance_by=line.allowance_by,
    )


# --- The company's FX rates and ERP -----------------------------------------------------------


class FxRateOut(BaseModel):
    id: uuid.UUID
    currency: str
    rate: Decimal
    source: str
    as_of: date
    created_at: datetime

    model_config = {"from_attributes": True}


class FxTableOut(BaseModel):
    buffer_percent: Decimal
    import_lines: list[dict[str, Any]]
    rates: list[FxRateOut]


class FxRateIn(BaseModel):
    currency: str = Field(min_length=3, max_length=3)
    rate: Decimal = Field(gt=0)
    source: str = Field(min_length=1, max_length=200)
    as_of: date


class ErpImportOut(BaseModel):
    imported: bool
    items: int
    purchase_orders: int
    historical_costs: int
    unchanged: int
    problems: list[dict[str, Any]]


def _fx_table(rows: list[FxRate]) -> FxTableOut:
    return FxTableOut(
        buffer_percent=landing.buffer_percent(),
        import_lines=[
            {
                "key": line.key,
                "label": line.label,
                "percent": str(line.percent),
                "applies_to": list(line.applies_to),
            }
            for line in landing.import_lines()
        ],
        rates=[FxRateOut.model_validate(row) for row in rows],
    )


@company_router.get("/fx-rates", response_model=FxTableOut)
def fx_rates(principal: CurrentPrincipal, session: DbSession) -> FxTableOut:
    """The recorded FX rates, newest first for each currency, with the configured buffer and
    import lines a foreign price takes on its way to SGD."""
    return _fx_table(costing.fx_table(session, principal.organisation_id))


@company_router.post("/fx-rates", response_model=FxTableOut, status_code=201)
def record_fx_rate(
    body: FxRateIn,
    session: DbSession,
    principal: Annotated[Principal, require(Action.RATE_LIBRARY_CHANGE)],
) -> FxTableOut:
    try:
        costing.record_fx(
            session,
            principal.organisation_id,
            principal.actor(),
            currency=body.currency,
            rate=body.rate,
            source=body.source,
            as_of=body.as_of,
        )
    except costing.CostingError as refusal:
        raise refused(refusal) from refusal
    return _fx_table(costing.fx_table(session, principal.organisation_id))


@company_router.post("/erp/import", response_model=ErpImportOut)
async def import_erp(
    file: Annotated[UploadFile, File()],
    session: DbSession,
    principal: Annotated[Principal, require(Action.RATE_LIBRARY_CHANGE)],
) -> ErpImportOut:
    """The ERP's export workbook (item master, purchase orders, historical costs), all or
    nothing. What it loads is history to compare prices with: it prices nothing."""
    payload = await file.read(MAX_ERP_BYTES + 1)
    if len(payload) > MAX_ERP_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "an export is at most 25 MB")
    if not payload.startswith(b"PK"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "an ERP export is an .xlsx workbook"
        )
    try:
        report = costing.import_erp_file(
            session,
            principal.organisation_id,
            payload,
            principal.actor(),
            file.filename or "erp.xlsx",
        )
    except costing.CostingError as refusal:
        raise refused(refusal, status.HTTP_422_UNPROCESSABLE_CONTENT) from refusal
    return ErpImportOut(
        imported=report.imported,
        items=report.items,
        purchase_orders=report.purchase_orders,
        historical_costs=report.historical_costs,
        unchanged=report.unchanged,
        problems=report.problems,
    )
