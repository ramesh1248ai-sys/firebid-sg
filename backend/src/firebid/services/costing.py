"""A bid's cost build-up, and what stands behind it (P2-04).

* **FX rates (FR-CST-04)** are recorded, each with its source and date, and never changed:
  a new rate is a new row, and a price is converted at the latest one on or before the day.
* **GST (FR-CST-05).** Prices are held exclusive of GST. The rate is configuration with an
  effective date, applied at the day the bid is priced (`bid_price_basis`), so a rate that
  changes later changes only bids priced from that date.
* **The build-up (FR-CST-06)** shows every component as its own line with its basis and
  source. The priced bill gives materials, fittings, valves and equipment; everything else
  is what an estimator entered, under their name (FR-CST-09). Nothing is filled in for them.
* **History (FR-CST-07).** Each priced line is compared with past purchase order and project
  prices for the same item key, and flagged when it is beyond the tolerance.
* **The ERP (FR-CST-08)** is read through `pricing.erp.ErpAdapter`. The file adapter loads
  the item master and past prices, all or nothing. History is for comparison: it never
  prices a line.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.audit import record_event
from firebid.db.models.commercial import BoqLine
from firebid.db.models.core import Bid
from firebid.db.models.costing import (
    BidPriceBasis,
    CostBuildupLine,
    ErpItem,
    FxRate,
    PriceHistory,
)
from firebid.domain.actors import Actor, AuditContext
from firebid.domain.values import Money
from firebid.pricing import buildup, erp, history
from firebid.pricing import landed as landing
from firebid.pricing.keys import unit_of


class CostingError(ValueError):
    """A costing request that cannot be done, with the reason a person can act on."""


def _context(bid: Bid) -> AuditContext:
    return AuditContext(organisation_id=bid.organisation_id, bid_id=bid.id)


# --- FX rates ---------------------------------------------------------------------------------


def record_fx(
    session: Session,
    organisation_id: uuid.UUID,
    actor: Actor,
    *,
    currency: str,
    rate: Decimal,
    source: str,
    as_of: date,
) -> FxRate:
    """Record how many SGD one unit of a currency is, where the figure is from, and when."""
    code = currency.strip().upper()
    if len(code) != 3 or not code.isalpha() or code == landing.HOME:
        raise CostingError("an FX rate is for a foreign currency, by its three-letter code")
    if rate <= 0:
        raise CostingError("an FX rate is more than zero")
    if not source.strip():
        raise CostingError("say where the rate is from")
    known = session.execute(
        select(FxRate).where(
            FxRate.organisation_id == organisation_id,
            FxRate.currency == code,
            FxRate.as_of == as_of,
            FxRate.source == source.strip(),
        )
    ).scalar_one_or_none()
    if known is not None:
        raise CostingError(f"a {code} rate from {source.strip()} on {as_of} is already recorded")
    row = FxRate(
        organisation_id=organisation_id,
        currency=code,
        rate=rate,
        source=source.strip()[:200],
        as_of=as_of,
        created_by_id=actor.id,
    )
    session.add(row)
    session.flush()
    record_event(
        session,
        context=AuditContext(organisation_id=organisation_id),
        actor=actor,
        action="FX rate: recorded",
        entity_type=FxRate.__tablename__,
        entity_id=row.id,
        after={"currency": code, "rate": str(rate), "source": row.source, "as_of": str(as_of)},
    )
    return row


def fx_table(session: Session, organisation_id: uuid.UUID) -> list[FxRate]:
    return list(
        session.execute(
            select(FxRate)
            .where(FxRate.organisation_id == organisation_id)
            .order_by(FxRate.currency, FxRate.as_of.desc())
        ).scalars()
    )


# --- The day a bid is priced ------------------------------------------------------------------


def priced_on(session: Session, bid_id: uuid.UUID) -> date | None:
    return session.execute(
        select(BidPriceBasis.priced_on).where(BidPriceBasis.bid_id == bid_id)
    ).scalar_one_or_none()


def set_priced_on(session: Session, bid: Bid, day: date, actor: Actor) -> BidPriceBasis:
    """Fix the day the bid is priced: the GST rate in force that day is the bid's."""
    row = session.execute(
        select(BidPriceBasis).where(BidPriceBasis.bid_id == bid.id)
    ).scalar_one_or_none()
    before = {"priced_on": str(row.priced_on)} if row else None
    if row is None:
        row = BidPriceBasis(bid_id=bid.id, priced_on=day, set_by=actor.label)
        session.add(row)
    else:
        row.priced_on = day
        row.set_by = actor.label
    session.flush()
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="bid: pricing date set",
        entity_type=BidPriceBasis.__tablename__,
        entity_id=row.id,
        before=before,
        after={"priced_on": str(day)},
    )
    return row


# --- What estimators enter --------------------------------------------------------------------


def entered(session: Session, bid_id: uuid.UUID) -> list[CostBuildupLine]:
    """The line in force for each component an estimator has entered."""
    return list(
        session.execute(
            select(CostBuildupLine)
            .where(CostBuildupLine.bid_id == bid_id, CostBuildupLine.retired_at.is_(None))
            .order_by(CostBuildupLine.created_at)
        ).scalars()
    )


def _retire(session: Session, bid_id: uuid.UUID, component: str) -> CostBuildupLine | None:
    old = session.execute(
        select(CostBuildupLine).where(
            CostBuildupLine.bid_id == bid_id,
            CostBuildupLine.component == component,
            CostBuildupLine.retired_at.is_(None),
        )
    ).scalar_one_or_none()
    if old is not None:
        old.retired_at = datetime.now(UTC)
    return old


def enter(
    session: Session,
    bid: Bid,
    actor: Actor,
    *,
    component: str,
    basis: str,
    amount: Decimal | None = None,
    percent: Decimal | None = None,
    base: str | None = None,
    note: str | None = None,
) -> CostBuildupLine:
    """An estimator's figure for a component: a lump sum, or a percentage of a base. It
    replaces the one before it, which is kept."""
    try:
        given = buildup.Entered(
            component=component,
            basis=basis,
            entered_by=actor.label,
            entered_on=datetime.now(UTC).date(),
            amount=Money.of(amount) if amount is not None else None,
            percent=percent,
            base=base,
            note=note,
        )
        given.check()
    except ValueError as refusal:
        raise CostingError(str(refusal)) from refusal
    old = _retire(session, bid.id, component)
    row = CostBuildupLine(
        bid_id=bid.id,
        component=component,
        basis=basis,
        amount=given.amount if basis == "lump_sum" else None,
        percent=percent if basis == "percentage" else None,
        base=base if basis == "percentage" else None,
        note=note,
        entered_by=actor.label,
        entered_by_id=actor.id,
    )
    session.add(row)
    session.flush()
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="cost build-up: entered",
        entity_type=CostBuildupLine.__tablename__,
        entity_id=row.id,
        before=_as_json(old) if old else None,
        after=_as_json(row),
        reason=note,
    )
    return row


def clear(session: Session, bid: Bid, component: str, actor: Actor) -> None:
    """Take an entered figure away: the component shows as not set again."""
    old = _retire(session, bid.id, component)
    if old is None:
        raise CostingError(f"nothing is entered for {component}")
    session.flush()
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="cost build-up: cleared",
        entity_type=CostBuildupLine.__tablename__,
        entity_id=old.id,
        before=_as_json(old),
    )


def _as_json(row: CostBuildupLine) -> dict[str, Any]:
    return {
        "component": row.component,
        "basis": row.basis,
        "amount": str(row.amount.amount) if row.amount is not None else None,
        "percent": str(row.percent) if row.percent is not None else None,
        "base": row.base,
        "entered_by": row.entered_by,
    }


# --- The build-up -----------------------------------------------------------------------------


def _bill(session: Session, bid_id: uuid.UUID) -> list[BoqLine]:
    from firebid.services.boq import current_boq, lines_of

    boq = current_boq(session, bid_id)
    return lines_of(session, boq) if boq is not None else []


def build_up(
    session: Session,
    bid: Bid,
    today: date | None = None,
    gst_rates: list[landing.GstRate] | None = None,
) -> buildup.BuildUp:
    """The bid's build-up as its bill is priced now, at its pricing date (today, where
    none is set)."""
    day = priced_on(session, bid.id) or today or datetime.now(UTC).date()
    bill = [
        buildup.BillLine(
            reference=line.item_no or line.description,
            group=line.group_heading or line.section or "",
            quantity=line.quantity,
            amount=line.amount,
            unit_rate=line.unit_rate,
            allowance_percent=line.allowance_percent,
            allowance=line.rate_id is None and line.amount is not None,
        )
        for line in _bill(session, bid.id)
    ]
    given = [
        buildup.Entered(
            component=row.component,
            basis=row.basis,
            entered_by=row.entered_by,
            entered_on=row.created_at.date(),
            amount=row.amount,
            percent=row.percent,
            base=row.base,
            note=row.note,
        )
        for row in entered(session, bid.id)
    ]
    # Labour comes from the labour estimate (P2-05), unless an estimator entered a figure.
    from firebid.services.labour import labour_basis

    labour = labour_basis(session, bid, today)
    return buildup.build(bill, given, day, gst_rates, [labour] if labour else None)


# --- History ----------------------------------------------------------------------------------


@dataclass
class LineComparison:
    line_id: uuid.UUID
    item_no: str | None
    description: str
    comparison: history.Comparison


def past_prices(session: Session, organisation_id: uuid.UUID) -> list[history.Past]:
    return [
        history.Past(row.item_key, row.unit, row.unit_price, row.on_date, row.kind, row.reference)
        for row in session.execute(
            select(PriceHistory).where(PriceHistory.organisation_id == organisation_id)
        ).scalars()
    ]


def comparisons(
    session: Session, bid: Bid, tolerance_percent: Decimal | None = None
) -> list[LineComparison]:
    """Every priced line of the bill beside the history for its item key and unit."""
    past = past_prices(session, bid.organisation_id)
    found = []
    for line in _bill(session, bid.id):
        if line.rate_id is None or line.unit_rate is None or not line.item_key:
            continue
        found.append(
            LineComparison(
                line_id=line.id,
                item_no=line.item_no,
                description=line.description,
                comparison=history.compare(
                    line.item_key,
                    unit_of(line.unit) or line.unit.lower(),
                    line.unit_rate.amount,
                    past,
                    tolerance_percent,
                ),
            )
        )
    return found


# --- The ERP ----------------------------------------------------------------------------------


@dataclass
class ErpReport:
    imported: bool
    items: int = 0
    purchase_orders: int = 0
    historical_costs: int = 0
    unchanged: int = 0
    problems: list[dict[str, Any]] = field(default_factory=list)


def load_erp(
    session: Session, organisation_id: uuid.UUID, adapter: erp.ErpAdapter, actor: Actor, label: str
) -> ErpReport:
    """What an ERP adapter gives, into the item master and the price history. A row already
    held is left as it is."""
    report = ErpReport(imported=True)
    items = {
        row.code: row
        for row in session.execute(
            select(ErpItem).where(ErpItem.organisation_id == organisation_id)
        ).scalars()
    }
    for item in adapter.item_master():
        held = items.get(item.code)
        if held is None:
            session.add(
                ErpItem(
                    organisation_id=organisation_id,
                    code=item.code[:80],
                    description=item.description,
                    unit=item.unit[:16],
                    item_key=item.item_key,
                )
            )
            report.items += 1
        elif (held.description, held.unit, held.item_key) != (
            item.description,
            item.unit,
            item.item_key,
        ):
            held.description, held.unit, held.item_key = item.description, item.unit, item.item_key
            report.items += 1
        else:
            report.unchanged += 1
    known = {
        (row.kind, row.reference, row.item_key, row.unit, row.on_date)
        for row in session.execute(
            select(PriceHistory).where(PriceHistory.organisation_id == organisation_id)
        ).scalars()
    }
    for kind, prices in (
        ("purchase_orders", adapter.purchase_orders()),
        ("historical_costs", adapter.historical_costs()),
    ):
        for price in prices:
            identity = (price.kind, price.reference, price.item_key, price.unit, price.on)
            if identity in known:
                report.unchanged += 1
                continue
            known.add(identity)
            session.add(
                PriceHistory(
                    organisation_id=organisation_id,
                    kind=price.kind,
                    reference=price.reference[:200],
                    on_date=price.on,
                    item_key=price.item_key,
                    unit=price.unit[:16],
                    unit_price=price.unit_price,
                    description=price.description,
                    supplier=price.supplier,
                )
            )
            setattr(report, kind, getattr(report, kind) + 1)
    session.flush()
    record_event(
        session,
        context=AuditContext(organisation_id=organisation_id),
        actor=actor,
        action="ERP: import",
        entity_type=PriceHistory.__tablename__,
        entity_id=str(organisation_id),
        after={
            "from": label,
            "items": report.items,
            "purchase_orders": report.purchase_orders,
            "historical_costs": report.historical_costs,
            "unchanged": report.unchanged,
        },
    )
    return report


def import_erp_file(
    session: Session, organisation_id: uuid.UUID, payload: bytes, actor: Actor, filename: str
) -> ErpReport:
    """The ERP's export workbook, all or nothing. Read in the sandbox."""
    from firebid.sandbox.runner import SandboxFailure, run_sandboxed

    try:
        found: dict[str, Any] = run_sandboxed(erp.read_json, payload)
    except SandboxFailure as failure:
        raise CostingError(f"the export could not be read: {failure.reason}") from failure
    adapter = erp.FileErp(found)
    if adapter.problems:
        return ErpReport(imported=False, problems=adapter.problems)
    return load_erp(session, organisation_id, adapter, actor, filename)
