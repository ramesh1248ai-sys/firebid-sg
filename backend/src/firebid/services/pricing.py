"""Pricing a bid's BOQ from the company rate library (P1-10, FR-CST-01).

* **The rate library** is the organisation's. Entries are immutable: importing a changed
  rate for the same item, unit and source makes a new version and retires the old one, which
  stays, with every line priced from it. An import with any error imports nothing.
* **Keys.** A BOQ line's item key comes from the QTO items behind it (`pricing.keys`).
* **Matching.** An entry with exactly the line's key prices the line (a rule match). Where
  entries differ only in something other than type and size, the model may propose one; an
  estimator confirms it, or chooses another. A line with neither is "unpriced".
* **Provenance.** `price_line` is the only place a price is set, from an entry by way of
  `domain.pricing.price_from`; the database refuses anything else (migration 0026).
* **Validity.** Each priced line is checked against today and the tender validity end.
* **Totals** are section and grand totals, excluding GST.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.audit import record_event
from firebid.db.models.commercial import Boq, BoqLine, BoqLineSource, Rate
from firebid.db.models.core import Bid
from firebid.db.models.takeoff import QtoItem
from firebid.domain.actors import Actor, AuditContext
from firebid.domain.pricing import Unsourced, price_from
from firebid.domain.values import Money
from firebid.pricing import rates as rules
from firebid.pricing.keys import ItemKey

log = structlog.get_logger("firebid.pricing")


class PricingError(ValueError):
    """A pricing request that cannot be done, with the reason a person can act on."""


def _context(bid: Bid) -> AuditContext:
    return AuditContext(organisation_id=bid.organisation_id, bid_id=bid.id)


# --- The rate library -----------------------------------------------------------------------


def current_rates(session: Session, organisation_id: uuid.UUID) -> list[Rate]:
    return list(
        session.execute(
            select(Rate)
            .where(Rate.organisation_id == organisation_id, Rate.retired_at.is_(None))
            .order_by(Rate.item_key, Rate.unit, Rate.source_type, Rate.source_reference)
        ).scalars()
    )


def entry_of(rate: Rate) -> rules.Entry:
    return rules.Entry(
        id=str(rate.id),
        key=ItemKey.parse(rate.item_key),
        unit=rate.unit,
        rate=rate.unit_rate,
        source_type=rate.source_type,
        source_reference=rate.source_reference,
        effective_from=rate.effective_from,
        valid_until=rate.valid_until,
        description=rate.description,
    )


def history(session: Session, rate: Rate) -> list[Rate]:
    """Every version of an entry, oldest first: the same item, unit, source and reference."""
    return list(
        session.execute(
            select(Rate)
            .where(
                Rate.organisation_id == rate.organisation_id,
                Rate.item_key == rate.item_key,
                Rate.unit == rate.unit,
                Rate.source_type == rate.source_type,
                Rate.source_reference == rate.source_reference,
            )
            .order_by(Rate.version)
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


def import_rates(
    session: Session, organisation_id: uuid.UUID, payload: bytes, actor: Actor, filename: str
) -> ImportReport:
    """A rate list workbook into the library, all or nothing. Read in the sandbox."""
    from firebid.pricing.importer import read_json
    from firebid.sandbox.runner import run_sandboxed

    found: dict[str, Any] = run_sandboxed(read_json, payload)
    report = ImportReport(imported=False, sheet=found.get("sheet"))
    if found["problems"]:
        report.problems = list(found["problems"])
        return report
    current = {
        (r.item_key, r.unit, r.source_type, r.source_reference): r
        for r in current_rates(session, organisation_id)
    }
    now = datetime.now(UTC)
    for row in found["rows"]:
        identity = (row["key"], row["unit"], row["source_type"], row["source_reference"])
        values = {
            "description": row["description"],
            "unit_rate": Money.of(Decimal(row["rate"])),
            "effective_from": date.fromisoformat(row["effective_from"]),
            "valid_until": date.fromisoformat(row["valid_until"]) if row["valid_until"] else None,
        }
        old = current.get(identity)
        if old is not None and all(getattr(old, k) == v for k, v in values.items()):
            report.unchanged += 1
            continue
        if old is not None:
            old.retired_at = now
            session.flush()  # the current-version index allows one current row
            report.superseded += 1
        else:
            report.created += 1
        session.add(
            Rate(
                organisation_id=organisation_id,
                item_key=row["key"],
                key_parts=row["parts"],
                unit=row["unit"],
                currency="SGD",
                source_type=row["source_type"],
                source_reference=row["source_reference"],
                version=(old.version + 1) if old else 1,
                supersedes_id=old.id if old else None,
                created_by_id=actor.id,
                **values,
            )
        )
    session.flush()
    report.imported = True
    record_event(
        session,
        context=AuditContext(organisation_id=organisation_id),
        actor=actor,
        action="rate library: import",
        entity_type=Rate.__tablename__,
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


# --- Line keys ------------------------------------------------------------------------------


def _value(attribute: object) -> str:
    return str(attribute.get("value") if isinstance(attribute, dict) else attribute or "")


def assign_keys(session: Session, boq: Boq) -> None:
    """Each line's item key, from what its QTO items agree on."""
    lines = {line.id: line for line in _lines(session, boq)}
    keys: dict[uuid.UUID, list[ItemKey]] = {}
    for line_id, item in session.execute(
        select(BoqLineSource.boq_line_id, QtoItem)
        .join(QtoItem, QtoItem.id == BoqLineSource.qto_item_id)
        .where(BoqLineSource.boq_line_id.in_(list(lines)))
    ).tuples():
        attributes = {name: _value(v) for name, v in dict(item.attributes or {}).items()}
        keys.setdefault(line_id, []).append(ItemKey.from_item(item.item_type, attributes))
    for line_id, line in lines.items():
        common = ItemKey.common(keys.get(line_id, []))
        line.item_key = common.text() if common and common.type else None
    session.flush()


def _lines(session: Session, boq: Boq) -> list[BoqLine]:
    return list(
        session.execute(
            select(BoqLine).where(BoqLine.boq_id == boq.id).order_by(BoqLine.sort_order)
        ).scalars()
    )


# --- Setting a price ------------------------------------------------------------------------


def price_line(
    session: Session,
    line: BoqLine,
    rate: Rate | None,
    *,
    method: str,
    actor: Actor,
    reason: str | None = None,
    provenance: dict[str, Any] | None = None,
) -> BoqLine:
    """Price a line from a rate entry: the only way a line gets a price."""
    if rate is not None and rate.unit.lower() != (line.unit or "").lower():
        from firebid.pricing.keys import unit_of

        if unit_of(rate.unit) != unit_of(line.unit):
            raise PricingError(f"the rate is per {rate.unit}; the line is measured in {line.unit}")
    try:
        price = price_from(rate, line.quantity)
    except Unsourced as refusal:
        raise PricingError(str(refusal)) from refusal
    line.rate_id = price.rate_id
    line.unit_rate = price.unit_rate
    line.amount = price.amount
    line.proposed_rate_id = None
    line.price_method = method
    line.price_reason = reason
    line.price_provenance = dict(provenance or {})
    line.priced_by_id = actor.id
    line.priced_at = datetime.now(UTC)
    session.flush()
    return line


def _unprice(line: BoqLine) -> None:
    line.rate_id = None
    line.unit_rate = None
    if not (line.is_provisional or line.is_lump_sum):
        line.amount = None
    line.price_method = None
    line.price_reason = None
    line.price_provenance = {}
    line.priced_by_id = None
    line.priced_at = None


# --- Pricing a BOQ --------------------------------------------------------------------------


@dataclass
class PricingRun:
    priced: int = 0
    proposed: int = 0
    unpriced: int = 0
    awaiting_model: int = 0


def price_boq(session: Session, bid: Bid, actor: Actor, router: Any | None = None) -> PricingRun:
    """Rule-price every line an exact entry exists for; ask the model about the rest.

    A price a person chose or confirmed is kept. Provisional and lump sums are the
    estimator's allowances and are not priced from the library.
    """
    from firebid.services.boq import current_boq

    boq = current_boq(session, bid.id)
    if boq is None:
        raise PricingError("build the BOQ first")
    assign_keys(session, boq)
    rates = {str(r.id): r for r in current_rates(session, bid.organisation_id)}
    entries = [entry_of(r) for r in rates.values()]
    run = PricingRun()
    open_lines: list[tuple[BoqLine, list[rules.Entry]]] = []
    for line in _lines(session, boq):
        if line.is_provisional or line.is_lump_sum or line.price_method == "person":
            continue
        key = ItemKey.parse(line.item_key) if line.item_key else None
        found = rules.exact(key, line.unit, entries) if key else None
        if found is not None:
            price_line(
                session,
                line,
                rates[found.id],
                method="rule",
                actor=actor,
                reason=f"the library entry for {key.label() if key else line.description}",
                provenance={"rule": "exact item key", "key": line.item_key},
            )
            run.priced += 1
            continue
        if line.price_method == "model":
            continue  # a confirmed proposal stands until someone changes it
        _unprice(line)
        options = rules.candidates(key, line.unit, entries) if key else []
        if not options:
            line.proposed_rate_id = None
            run.unpriced += 1
            continue
        open_lines.append((line, options))
    if open_lines:
        if router is None:
            run.awaiting_model = len(open_lines)
            for line, _ in open_lines:
                line.price_provenance = {"awaiting_model": True}
        else:
            run.proposed += _ask_model(session, bid, open_lines, router)
        run.unpriced += sum(1 for line, _ in open_lines if line.proposed_rate_id is None)
    session.flush()
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="BOQ: priced from the rate library",
        entity_type=Boq.__tablename__,
        entity_id=boq.id,
        after={
            "priced": run.priced,
            "proposed": run.proposed,
            "unpriced": run.unpriced,
            "awaiting_model": run.awaiting_model,
        },
    )
    return run


def _ask_model(
    session: Session, bid: Bid, open_lines: list[tuple[BoqLine, list[rules.Entry]]], router: Any
) -> int:
    from firebid.agents.base import AgentInput
    from firebid.agents.rate_match import (
        CandidateIn,
        LineIn,
        RateMatchAnswer,
        RateMatchBatch,
        RateMatcher,
    )
    from firebid.agents.runtime import Escalated, run_agent_with_result

    by_ref = {str(line.id): line for line, _ in open_lines}
    batch = RateMatchBatch(
        lines=[
            LineIn(
                ref=str(line.id),
                description=line.description,
                unit=line.unit,
                key=ItemKey.parse(line.item_key or "").label(),
                candidates=[
                    CandidateIn(
                        id=e.id,
                        description=e.description,
                        key=e.key.label(),
                        source_type=e.source_type,
                    )
                    for e in options
                ],
            )
            for line, options in open_lines
        ]
    )
    for line, _ in open_lines:
        line.price_provenance = {}
    fingerprint = hashlib.sha256(batch.model_dump_json().encode()).hexdigest()[:24]
    try:
        agent_run, result = run_agent_with_result(
            session,
            RateMatcher(router),
            AgentInput(bid_id=bid.id, idempotency_key=f"rate-match:{fingerprint}", payload=batch),
        )
    except Escalated:
        return 0
    if result is None or not isinstance(result.output, RateMatchAnswer):
        return 0
    proposed = 0
    for choice in result.output.choices:
        chosen = by_ref.get(choice.line)
        if chosen is None:
            continue
        chosen.proposed_rate_id = uuid.UUID(choice.entry) if choice.entry else None
        chosen.price_reason = choice.reason
        chosen.price_provenance = {
            "agent_run_id": str(agent_run.id),
            "model": agent_run.model,
            "prompt_version": agent_run.prompt_version,
            "confidence": choice.confidence,
        }
        proposed += chosen.proposed_rate_id is not None
    session.flush()
    return proposed


def confirm_proposal(session: Session, bid: Bid, line: BoqLine, actor: Actor) -> BoqLine:
    """An estimator accepts the entry the model proposed. The price is the entry's."""
    if line.proposed_rate_id is None:
        raise PricingError("nothing is proposed for this line")
    rate = session.get(Rate, line.proposed_rate_id)
    if rate is None or rate.retired_at is not None:
        raise PricingError("the proposed entry is no longer current: choose again")
    provenance = dict(line.price_provenance or {})
    reason = line.price_reason
    price_line(
        session, line, rate, method="model", actor=actor, reason=reason, provenance=provenance
    )
    _audit(session, bid, line, actor, "BOQ line: proposed rate confirmed", rate)
    return line


def choose_rate(
    session: Session,
    bid: Bid,
    line: BoqLine,
    rate_id: uuid.UUID | None,
    actor: Actor,
    note: str | None = None,
) -> BoqLine:
    """An estimator prices a line from an entry of their choosing, or leaves it unpriced."""
    if rate_id is None:
        _unprice(line)
        line.proposed_rate_id = None
        line.price_method = "person"  # a person decided it has no library price
        line.price_reason = note or "left unpriced by a person"
        session.flush()
        _audit(session, bid, line, actor, "BOQ line: left unpriced", None)
        return line
    rate = session.get(Rate, rate_id)
    if rate is None or rate.organisation_id != bid.organisation_id:
        raise PricingError("no such rate library entry")
    if rate.retired_at is not None:
        raise PricingError("that entry has been superseded: choose its current version")
    price_line(
        session, line, rate, method="person", actor=actor, reason=note or "chosen by a person"
    )
    _audit(session, bid, line, actor, "BOQ line: rate chosen", rate)
    return line


def _audit(
    session: Session, bid: Bid, line: BoqLine, actor: Actor, action: str, rate: Rate | None
) -> None:
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action=action,
        entity_type=BoqLine.__tablename__,
        entity_id=line.id,
        after={
            "rate_id": str(rate.id) if rate else None,
            "source": f"{rate.source_type}: {rate.source_reference}" if rate else None,
            "method": line.price_method,
        },
        reason=line.price_reason,
    )


def after_build(session: Session, bid: Bid, previous: Boq | None, boq: Boq, actor: Actor) -> None:
    """A rebuilt BOQ keeps the prices people chose or confirmed, line by line, then the rules
    price the rest."""
    assign_keys(session, boq)
    if previous is not None:
        kept = {
            line.line_key: line
            for line in _lines(session, previous)
            if line.line_key and line.price_method in ("person", "model") and line.rate_id
        }
        for line in _lines(session, boq):
            old = kept.get(line.line_key) if line.line_key else None
            rate = session.get(Rate, old.rate_id) if old and old.rate_id else None
            if old is None or rate is None or rate.retired_at is not None:
                continue
            price_line(
                session,
                line,
                rate,
                method=str(old.price_method),
                actor=actor,
                reason=old.price_reason,
                provenance=dict(old.price_provenance or {}),
            )
    if current_rates(session, bid.organisation_id):
        price_boq(session, bid, actor)


# --- What a line's price is, and what adds up -----------------------------------------------


@dataclass
class LinePrice:
    status: str  # priced | proposed | unpriced | allowance
    rate: Rate | None
    proposed: Rate | None
    warnings: list[rules.Warning]
    superseded: bool = False


def line_prices(
    session: Session, bid: Bid, boq: Boq, today: date | None = None
) -> dict[uuid.UUID, LinePrice]:
    today = today or datetime.now(UTC).date()
    end = rules.tender_validity_end(bid.submission_deadline, bid.tender_validity_days)
    lines = _lines(session, boq)
    wanted = {line.rate_id for line in lines if line.rate_id} | {
        line.proposed_rate_id for line in lines if line.proposed_rate_id
    }
    rates = (
        {r.id: r for r in session.execute(select(Rate).where(Rate.id.in_(wanted))).scalars()}
        if wanted
        else {}
    )
    out = {}
    for line in lines:
        rate = rates.get(line.rate_id) if line.rate_id else None
        proposed = rates.get(line.proposed_rate_id) if line.proposed_rate_id else None
        if rate is not None:
            status = "priced"
        elif line.amount is not None and (line.is_provisional or line.is_lump_sum):
            status = "allowance"
        elif proposed is not None:
            status = "proposed"
        else:
            status = "unpriced"
        out[line.id] = LinePrice(
            status=status,
            rate=rate,
            proposed=proposed,
            warnings=rules.warnings(entry_of(rate), today, end) if rate else [],
            superseded=bool(rate and rate.retired_at is not None),
        )
    return out


def boq_totals(session: Session, boq: Boq) -> rules.Totals:
    return rules.totals(
        [
            rules.PricedLine(
                section=line.section,
                quantity=line.quantity,
                rate=line.unit_rate if line.rate_id else None,
                allowance=line.amount
                if line.rate_id is None and (line.is_provisional or line.is_lump_sum)
                else None,
            )
            for line in _lines(session, boq)
        ]
    )


def unit_rates_by_item(session: Session, bid_id: uuid.UUID) -> dict[uuid.UUID, Money]:
    """Each QTO item's unit rate, where the current BOQ line it is in is priced: what a
    mistake on it costs, for the review queue."""
    from firebid.services.boq import current_boq

    boq = current_boq(session, bid_id)
    if boq is None:
        return {}
    rows = session.execute(
        select(BoqLineSource.qto_item_id, BoqLine.unit_rate)
        .join(BoqLine, BoqLine.id == BoqLineSource.boq_line_id)
        .where(BoqLine.boq_id == boq.id, BoqLine.rate_id.is_not(None))
    ).tuples()
    return {item_id: rate for item_id, rate in rows if rate is not None}


def fingerprint_of(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()
