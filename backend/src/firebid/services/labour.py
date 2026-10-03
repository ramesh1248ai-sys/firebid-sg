"""Labour estimation for a bid (P2-05; FR-LAB-01, 02, 03).

* **The productivity library** is the organisation's. Entries are immutable: importing a
  changed figure for the same item and unit makes a new version and retires the old one.
  An import with any error imports nothing. Every entry has a source.
* **Conditions.** The platform proposes multipliers from the bid's parameters (a height
  band from a ceiling height, basement from a level's name, high-rise from the levels
  served). An estimator confirms, rejects or adds them, for the bid or for a level. Only a
  confirmed one is applied.
* **The estimate** is worked out from the current bill each time it is asked for: baseline
  hours from the library, each confirmed multiplier, and the trade's hourly rate from the
  rate table in force on the day the bid is priced. Nothing is stored, so nothing goes stale.
* **The cost build-up's labour line** (P2-04) is the estimate's cost, with its basis, unless
  an estimator has entered their own figure.
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
from firebid.db.models.labour import LabourCondition, LabourProductivity
from firebid.db.models.takeoff import BidParameter
from firebid.domain.actors import Actor, AuditContext
from firebid.labour import estimate as est
from firebid.labour import multipliers, productivity, rates
from firebid.pricing import buildup
from firebid.pricing.keys import ItemKey, unit_of


class LabourError(ValueError):
    """A labour request that cannot be done, with the reason a person can act on."""


def _context(bid: Bid) -> AuditContext:
    return AuditContext(organisation_id=bid.organisation_id, bid_id=bid.id)


# --- The productivity library -----------------------------------------------------------------


def current_entries(session: Session, organisation_id: uuid.UUID) -> list[LabourProductivity]:
    return list(
        session.execute(
            select(LabourProductivity)
            .where(
                LabourProductivity.organisation_id == organisation_id,
                LabourProductivity.retired_at.is_(None),
            )
            .order_by(
                LabourProductivity.item_type, LabourProductivity.dn, LabourProductivity.joining
            )
        ).scalars()
    )


def entry_of(row: LabourProductivity) -> productivity.Entry:
    return productivity.Entry(
        id=str(row.id),
        type=row.item_type,
        dn=row.dn,
        joining=row.joining,
        unit=row.unit,
        hours=row.hours_per_unit,
        trade=row.trade,
        source_type=row.source_type,
        source_reference=row.source_reference,
        description=row.description,
    )


def history(session: Session, row: LabourProductivity) -> list[LabourProductivity]:
    """Every version of an entry, oldest first: the same item and unit."""
    return list(
        session.execute(
            select(LabourProductivity)
            .where(
                LabourProductivity.organisation_id == row.organisation_id,
                LabourProductivity.item_type == row.item_type,
                LabourProductivity.dn == row.dn,
                LabourProductivity.joining == row.joining,
                LabourProductivity.unit == row.unit,
            )
            .order_by(LabourProductivity.version)
        ).scalars()
    )


@dataclass
class ImportReport:
    imported: bool
    sheet: str | None = None
    created: int = 0
    superseded: int = 0
    unchanged: int = 0
    problems: list[dict[str, Any]] = field(default_factory=list)


def _put(
    session: Session,
    organisation_id: uuid.UUID,
    actor: Actor,
    current: dict[tuple[str, str, str, str], LabourProductivity],
    values: dict[str, Any],
    report: ImportReport,
) -> LabourProductivity | None:
    """One entry into the library: a new version where the figures differ, nothing where
    they do not."""
    entry = productivity.Entry(
        id="",
        type=values["type"],
        dn=values["dn"],
        joining=values["joining"],
        unit=values["unit"],
        hours=Decimal(values["hours"]),
        trade=values["trade"],
        source_type=values["source_type"],
        source_reference=values["source_reference"],
    )
    try:
        productivity.check(entry)
    except ValueError as refusal:
        raise LabourError(str(refusal)) from refusal
    identity = (entry.type, entry.dn, entry.joining, entry.unit)
    old = current.get(identity)
    stated = {
        "description": values["description"],
        "hours_per_unit": entry.hours,
        "trade": entry.trade,
        "source_type": entry.source_type,
        "source_reference": entry.source_reference,
    }
    if old is not None and all(getattr(old, name) == value for name, value in stated.items()):
        report.unchanged += 1
        return None
    if old is not None:
        old.retired_at = datetime.now(UTC)
        session.flush()  # the current-version index allows one current row
        report.superseded += 1
    else:
        report.created += 1
    row = LabourProductivity(
        organisation_id=organisation_id,
        item_type=entry.type,
        dn=entry.dn,
        joining=entry.joining,
        unit=entry.unit,
        version=(old.version + 1) if old else 1,
        supersedes_id=old.id if old else None,
        created_by_id=actor.id,
        **stated,
    )
    session.add(row)
    session.flush()
    current[identity] = row
    return row


def _current_by_identity(
    session: Session, organisation_id: uuid.UUID
) -> dict[tuple[str, str, str, str], LabourProductivity]:
    return {
        (row.item_type, row.dn, row.joining, row.unit): row
        for row in current_entries(session, organisation_id)
    }


def import_productivity(
    session: Session, organisation_id: uuid.UUID, payload: bytes, actor: Actor, filename: str
) -> ImportReport:
    """A productivity list workbook into the library, all or nothing. Read in the sandbox."""
    from firebid.labour.importer import read_json
    from firebid.sandbox.runner import SandboxFailure, run_sandboxed

    try:
        found: dict[str, Any] = run_sandboxed(read_json, payload)
    except SandboxFailure as failure:
        raise LabourError(f"the list could not be read: {failure.reason}") from failure
    report = ImportReport(imported=False, sheet=found.get("sheet"))
    if found["problems"]:
        report.problems = list(found["problems"])
        return report
    current = _current_by_identity(session, organisation_id)
    for row in found["rows"]:
        _put(session, organisation_id, actor, current, row, report)
    report.imported = True
    record_event(
        session,
        context=AuditContext(organisation_id=organisation_id),
        actor=actor,
        action="productivity library: import",
        entity_type=LabourProductivity.__tablename__,
        entity_id=str(organisation_id),
        after={
            "file": filename,
            "sheet": report.sheet,
            "created": report.created,
            "superseded": report.superseded,
            "unchanged": report.unchanged,
        },
    )
    return report


def set_entry(
    session: Session,
    organisation_id: uuid.UUID,
    actor: Actor,
    *,
    item_type: str,
    unit: str,
    hours: Decimal,
    trade: str,
    description: str,
    source_type: str,
    source_reference: str | None = None,
    dn: str = "",
    joining: str = "",
) -> LabourProductivity:
    """One entry, as a person gives it. An estimator's judgement is under their name."""
    if source_type == "estimator_judgement" and not (source_reference or "").strip():
        source_reference = actor.label
    key = ItemKey.of(type=item_type, dn=dn, joining=joining)
    canonical = unit_of(unit)
    if canonical is None:
        raise LabourError(f"{unit!r} is not a known unit")
    report = ImportReport(imported=True)
    row = _put(
        session,
        organisation_id,
        actor,
        _current_by_identity(session, organisation_id),
        {
            "type": key.type,
            "dn": key.dn,
            "joining": key.joining,
            "unit": canonical,
            "hours": str(hours),
            "trade": trade.strip().lower().replace(" ", "_"),
            "description": description.strip(),
            "source_type": source_type,
            "source_reference": (source_reference or "").strip(),
        },
        report,
    )
    if row is None:
        raise LabourError("the library already holds this entry with these figures")
    record_event(
        session,
        context=AuditContext(organisation_id=organisation_id),
        actor=actor,
        action="productivity library: entry",
        entity_type=LabourProductivity.__tablename__,
        entity_id=row.id,
        after={
            "item": f"{row.item_type}|{row.dn}|{row.joining}",
            "unit": row.unit,
            "hours_per_unit": str(row.hours_per_unit),
            "source": f"{row.source_type}: {row.source_reference}",
            "version": row.version,
        },
    )
    return row


# --- Conditions -------------------------------------------------------------------------------


def conditions(session: Session, bid_id: uuid.UUID) -> list[LabourCondition]:
    return list(
        session.execute(
            select(LabourCondition)
            .where(LabourCondition.bid_id == bid_id)
            .order_by(LabourCondition.level.nulls_first(), LabourCondition.created_at)
        ).scalars()
    )


def _bill(session: Session, bid_id: uuid.UUID) -> list[BoqLine]:
    from firebid.services.boq import current_boq, lines_of

    boq = current_boq(session, bid_id)
    return lines_of(session, boq) if boq is not None else []


def _parameters(
    session: Session, bid_id: uuid.UUID, name: str
) -> dict[str | None, tuple[Decimal, str]]:
    """The value in force for a parameter, for the bid (None) and for each level."""
    rows = session.execute(
        select(BidParameter)
        .where(BidParameter.bid_id == bid_id, BidParameter.name == name)
        .order_by(BidParameter.created_at, BidParameter.id)
    ).scalars()
    return {row.level: (row.value, row.source) for row in rows}  # the last stated wins


def propose(session: Session, bid: Bid, actor: Actor) -> list[LabourCondition]:
    """Propose multipliers from the bid's parameters and its bill's levels. What is already
    proposed, confirmed or rejected for a level is left as it is."""
    levels = sorted({line.level for line in _bill(session, bid.id) if line.level})
    served = _parameters(session, bid.id, "levels_served").get(None)
    found = multipliers.proposals(_parameters(session, bid.id, "ceiling_height_mm"), levels, served)
    held = {(row.level, row.multiplier_key) for row in conditions(session, bid.id)}
    made = []
    for proposal in found:
        if (proposal.level, proposal.key) in held:
            continue
        held.add((proposal.level, proposal.key))
        row = LabourCondition(
            bid_id=bid.id,
            level=proposal.level,
            multiplier_key=proposal.key,
            state="proposed",
            basis=proposal.basis,
            proposed_by="platform",
        )
        session.add(row)
        made.append(row)
    session.flush()
    if made:
        record_event(
            session,
            context=_context(bid),
            actor=actor,
            action="labour conditions: proposed",
            entity_type=LabourCondition.__tablename__,
            entity_id=str(bid.id),
            after={"proposed": [f"{row.level or 'bid'}: {row.multiplier_key}" for row in made]},
        )
    return made


def decide(
    session: Session,
    bid: Bid,
    actor: Actor,
    *,
    key: str,
    level: str | None,
    state: str,
    basis: str | None = None,
) -> LabourCondition:
    """An estimator confirms or rejects a multiplier for the bid or a level, or adds one the
    platform did not propose."""
    if state not in ("confirmed", "rejected"):
        raise LabourError("a multiplier is confirmed or rejected")
    if actor.id is None:
        raise LabourError("a multiplier is confirmed by a named person")
    if key not in multipliers.by_key():
        raise LabourError(f"{key!r} is not a multiplier of the catalogue")
    level = (level or "").strip() or None
    row = session.execute(
        select(LabourCondition).where(
            LabourCondition.bid_id == bid.id,
            LabourCondition.multiplier_key == key,
            LabourCondition.level.is_(None) if level is None else LabourCondition.level == level,
        )
    ).scalar_one_or_none()
    before = {"state": row.state} if row else None
    if row is None:
        if not (basis or "").strip():
            raise LabourError("say why the multiplier applies")
        row = LabourCondition(
            bid_id=bid.id,
            level=level,
            multiplier_key=key,
            basis=(basis or "").strip(),
            proposed_by=actor.label,
        )
        session.add(row)
    elif (basis or "").strip():
        row.basis = (basis or "").strip()
    row.state = state
    row.decided_by = actor.label
    row.decided_by_id = actor.id
    row.decided_at = datetime.now(UTC)
    session.flush()
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action=f"labour condition: {state}",
        entity_type=LabourCondition.__tablename__,
        entity_id=row.id,
        before=before,
        after={"multiplier": key, "level": level, "state": state},
        reason=row.basis or None,
    )
    return row


# --- The estimate -----------------------------------------------------------------------------


def pricing_day(session: Session, bid: Bid, today: date | None = None) -> date:
    from firebid.services.costing import priced_on

    return priced_on(session, bid.id) or today or datetime.now(UTC).date()


def estimate(
    session: Session,
    bid: Bid,
    today: date | None = None,
    rate_tables: list[rates.Table] | None = None,
) -> est.Estimate:
    """Labour hours and cost for every measured line of the current bill."""
    day = pricing_day(session, bid, today)
    entries = [entry_of(row) for row in current_entries(session, bid.organisation_id)]
    catalogue = multipliers.by_key()
    confirmed = [
        multipliers.Condition(
            key=row.multiplier_key,
            level=row.level,
            confirmed_by=row.decided_by or "",
            basis=row.basis,
        )
        for row in conditions(session, bid.id)
        if row.state == "confirmed"
    ]
    trades: dict[str, rates.TradeRate | None] = {}

    def rate_of(trade: str) -> rates.TradeRate | None:
        if trade not in trades:
            try:
                trades[trade] = rates.trade_rate(trade, day, rate_tables)
            except ValueError:
                trades[trade] = None
        return trades[trade]

    lines = []
    for line in _bill(session, bid.id):
        if line.is_provisional or line.is_lump_sum:
            continue  # an estimator's allowance, not a measured quantity
        bill_line = est.BillLine(
            id=str(line.id),
            reference=line.item_no or "",
            description=line.description,
            section=line.section or "",
            level=line.level,
            unit=line.unit,
            quantity=line.quantity,
        )
        key = ItemKey.parse(line.item_key) if line.item_key else None
        entry = productivity.match(key, line.unit, entries)
        lines.append(
            est.line_hours(
                bill_line,
                entry,
                multipliers.applied_to(line.level, confirmed, catalogue),
                rate_of(entry.trade) if entry else None,
            )
        )
    return est.estimate(lines)


def labour_basis(
    session: Session, bid: Bid, today: date | None = None
) -> buildup.Calculated | None:
    """The estimate as the cost build-up's labour line, or None while there are no hours."""
    found = estimate(session, bid, today)
    costed = [line for line in found.lines if line.cost is not None]
    if not costed:
        return None
    table = rates.table_on(pricing_day(session, bid, today))
    detail = (
        f"{found.hours} man-hours on {len(costed)} bill line(s) "
        f"({found.baseline_hours} baseline, before multipliers)"
    )
    if found.without_hours:
        detail += f"; {found.without_hours} line(s) with no productivity entry are not included"
    return buildup.Calculated(
        component="labour",
        amount=found.cost,
        detail=detail,
        source=(
            "the productivity library and the labour rate table from "
            f"{table.effective_from.isoformat()} ({table.source})"
        ),
    )
