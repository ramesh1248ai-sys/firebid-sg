"""The company rate library and pricing a bid's BOQ from it (FR-CST-01).

A price comes only from a library entry: the rules price exact matches, the model proposes
among close ones (on the worker, `pricing.match`), and an estimator confirms a proposal or
chooses an entry. A line with none is "unpriced".
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from firebid.api.deps import CurrentBid, CurrentPrincipal, DbSession, require
from firebid.auth.permissions import Action
from firebid.auth.provisioning import Principal
from firebid.db.models.commercial import BoqLine, Rate
from firebid.pricing import rates as rules
from firebid.pricing.keys import ItemKey
from firebid.services import boq, pricing

rates_router = APIRouter(prefix="/rates", tags=["pricing"])
router = APIRouter(prefix="/bids/{bid_id}/pricing", tags=["pricing"])

MAX_RATE_LIST_BYTES = 10 * 1024 * 1024


# --- The rate library -----------------------------------------------------------------------


class RateOut(BaseModel):
    id: uuid.UUID
    item_key: str
    label: str
    key_parts: dict[str, str]
    description: str
    unit: str
    unit_rate: Decimal
    source_type: str
    source_reference: str
    effective_from: date
    valid_until: date | None
    version: int
    retired_at: datetime | None


class ProblemOut(BaseModel):
    row: int
    column: str
    message: str


class ImportOut(BaseModel):
    imported: bool
    sheet: str | None
    created: int
    superseded: int
    unchanged: int
    problems: list[ProblemOut]


def rate_out(row: Rate) -> RateOut:
    return RateOut(
        id=row.id,
        item_key=row.item_key,
        label=ItemKey.parse(row.item_key).label(),
        key_parts=dict(row.key_parts or {}),
        description=row.description,
        unit=row.unit,
        unit_rate=row.unit_rate.amount,
        source_type=row.source_type,
        source_reference=row.source_reference,
        effective_from=row.effective_from,
        valid_until=row.valid_until,
        version=row.version,
        retired_at=row.retired_at,
    )


@rates_router.get("", response_model=list[RateOut])
def list_rates(principal: CurrentPrincipal, session: DbSession) -> list[RateOut]:
    """The library's current entries."""
    return [rate_out(row) for row in pricing.current_rates(session, principal.organisation_id)]


@rates_router.get("/{rate_id}/history", response_model=list[RateOut])
def rate_history(
    rate_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession
) -> list[RateOut]:
    row = session.get(Rate, rate_id)
    if row is None or row.organisation_id != principal.organisation_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such rate")
    return [rate_out(version) for version in pricing.history(session, row)]


@rates_router.post("/import", response_model=ImportOut)
async def import_rates(
    file: Annotated[UploadFile, File()],
    session: DbSession,
    principal: Annotated[Principal, require(Action.RATE_LIBRARY_CHANGE)],
) -> ImportOut:
    """A rate list workbook, all or nothing: any problem imports nothing and is reported."""
    payload = await file.read(MAX_RATE_LIST_BYTES + 1)
    if len(payload) > MAX_RATE_LIST_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "a rate list is at most 10 MB"
        )
    if not payload.startswith(b"PK"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "a rate list is an .xlsx workbook"
        )
    report = pricing.import_rates(
        session,
        principal.organisation_id,
        payload,
        principal.actor(),
        file.filename or "rates.xlsx",
    )
    return ImportOut(
        imported=report.imported,
        sheet=report.sheet,
        created=report.created,
        superseded=report.superseded,
        unchanged=report.unchanged,
        problems=[ProblemOut(**problem) for problem in report.problems],
    )


# --- Pricing a bid's BOQ --------------------------------------------------------------------


class WarningOut(BaseModel):
    code: str
    message: str


class LinePriceOut(BaseModel):
    line_id: uuid.UUID
    item_no: str | None
    section: str | None
    description: str
    unit: str
    quantity: Decimal
    item_key: str | None
    status: str  # priced | proposed | unpriced | allowance
    unit_rate: Decimal | None
    amount: Decimal | None
    method: str | None
    reason: str | None
    rate: RateOut | None
    proposed: RateOut | None
    superseded: bool
    warnings: list[WarningOut]
    awaiting_model: bool


class TotalsOut(BaseModel):
    sections: dict[str, Decimal]
    priced: Decimal
    allowances: Decimal
    grand: Decimal
    unpriced: int
    gst_included: bool


class PricingOut(BaseModel):
    tender_validity_end: date | None
    lines: list[LinePriceOut]
    totals: TotalsOut


class ChoiceIn(BaseModel):
    rate_id: uuid.UUID | None
    note: str | None = None


def pricing_out(session: DbSession, context: CurrentBid) -> PricingOut:
    bid = context.bid
    current = boq.current_boq(session, bid.id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no BOQ is built yet")
    found = pricing.line_prices(session, bid, current)
    totals = pricing.boq_totals(session, current)
    lines = []
    for line in boq.lines_of(session, current):
        price = found[line.id]
        lines.append(
            LinePriceOut(
                line_id=line.id,
                item_no=line.item_no,
                section=line.section,
                description=line.description,
                unit=line.unit,
                quantity=line.quantity,
                item_key=line.item_key,
                status=price.status,
                unit_rate=line.unit_rate.amount if line.unit_rate else None,
                amount=line.amount.amount if line.amount else None,
                method=line.price_method,
                reason=line.price_reason,
                rate=rate_out(price.rate) if price.rate else None,
                proposed=rate_out(price.proposed) if price.proposed else None,
                superseded=price.superseded,
                warnings=[WarningOut(code=w.code, message=w.message) for w in price.warnings],
                awaiting_model=bool((line.price_provenance or {}).get("awaiting_model")),
            )
        )
    return PricingOut(
        tender_validity_end=rules.tender_validity_end(
            bid.submission_deadline, bid.tender_validity_days
        ),
        lines=lines,
        totals=TotalsOut(
            sections={name: amount.amount for name, amount in totals.sections.items()},
            priced=totals.priced.amount,
            allowances=totals.allowances.amount,
            grand=totals.grand.amount,
            unpriced=totals.unpriced,
            gst_included=totals.gst_included,
        ),
    )


def _line(session: DbSession, context: CurrentBid, line_id: uuid.UUID) -> BoqLine:
    line = session.get(BoqLine, line_id)
    current = boq.current_boq(session, context.bid.id)
    if line is None or current is None or line.boq_id != current.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such line in the current BOQ")
    return line


def refused(error: pricing.PricingError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(error))


@router.get("", response_model=PricingOut)
def get_pricing(context: CurrentBid, session: DbSession) -> PricingOut:
    """Every line's price and where it comes from, its warnings, and the totals (ex GST)."""
    return pricing_out(session, context)


@router.post("/run", response_model=PricingOut)
def run_pricing(
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.BOQ_EDIT)],
) -> PricingOut:
    """The rules price exact matches now; the model is asked about close ones on the worker."""
    try:
        found = pricing.price_boq(session, context.bid, principal.actor())
    except pricing.PricingError as refusal:
        raise refused(refusal) from refusal
    if found.awaiting_model:
        from firebid.jobs.enqueue import enqueue
        from firebid.jobs.tasks import match_rates_job

        actor = principal.actor()
        enqueue(
            session,
            match_rates_job,
            bid_id=str(context.bid.id),
            user_id=str(actor.id) if actor.id else "",
        )
    return pricing_out(session, context)


@router.post("/lines/{line_id}/confirm", response_model=PricingOut)
def confirm(
    line_id: uuid.UUID,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.BOQ_EDIT)],
) -> PricingOut:
    """Accept the entry proposed for a line. The price is the entry's."""
    try:
        pricing.confirm_proposal(
            session, context.bid, _line(session, context, line_id), principal.actor()
        )
    except pricing.PricingError as refusal:
        raise refused(refusal) from refusal
    return pricing_out(session, context)


@router.post("/lines/{line_id}/rate", response_model=PricingOut)
def choose(
    line_id: uuid.UUID,
    body: ChoiceIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.BOQ_EDIT)],
) -> PricingOut:
    """Price a line from an entry a person chose, or leave it unpriced (`rate_id: null`)."""
    try:
        pricing.choose_rate(
            session,
            context.bid,
            _line(session, context, line_id),
            body.rate_id,
            principal.actor(),
            body.note,
        )
    except pricing.PricingError as refusal:
        raise refused(refusal) from refusal
    return pricing_out(session, context)


@router.get("/lines/{line_id}/candidates", response_model=list[RateOut])
def candidates(line_id: uuid.UUID, context: CurrentBid, session: DbSession) -> list[RateOut]:
    """Entries a person may choose for a line: exact matches first, then close ones."""
    line = _line(session, context, line_id)
    current = {str(r.id): r for r in pricing.current_rates(session, context.bid.organisation_id)}
    entries = [pricing.entry_of(r) for r in current.values()]
    key = ItemKey.parse(line.item_key) if line.item_key else None
    found: list[Any] = []
    if key is not None:
        found = [e for e in entries if e.key == key] + rules.candidates(key, line.unit, entries)
    return [rate_out(current[e.id]) for e in found]
