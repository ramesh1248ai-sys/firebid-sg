"""Supplier quotations: captured, checked by a person, and made into rates (P2-04).

* **Capture (FR-CST-02).** A quotation file is stored, scanned, and only then read, in the
  sandbox (guardrail 9). The rules propose its fields and lines, each with the line of the
  file it was read from; the model may be asked for what the rules could not read, and its
  answers are kept only where the line it cites bears them out. A person corrects what is
  wrong, links each line to a rate-library item key or a BOQ line, and confirms.
* **A confirmed line becomes a rate-library entry** whose source is the quotation, so a
  bill is priced from it exactly as from any other entry (FR-CST-03). The entry's rate is
  the line's landed cost in SGD, with every step kept: the FX rate, its source and date, the
  buffer and each import line (FR-CST-04). Nothing here works out a price a supplier did
  not quote.
* **Flags (FR-CST-03).** An expired quotation, one whose validity ends before the tender's,
  and one with exclusions are flagged wherever the quotation is shown.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.audit import record_event
from firebid.db.models.commercial import BoqLine, Rate
from firebid.db.models.core import Bid
from firebid.db.models.costing import FxRate, Quotation, QuotationLine
from firebid.domain.actors import Actor, AuditContext
from firebid.ingest.scanning import Scanner, Verdict
from firebid.parsing import quotation as reader
from firebid.pricing import landed as landing
from firebid.pricing import quotation as rules
from firebid.pricing.keys import ItemKey, unit_of
from firebid.pricing.rates import tender_validity_end
from firebid.storage.object_store import ObjectExists, ObjectStore

log = structlog.get_logger("firebid.quotations")

FIELDS = (
    "supplier",
    "quote_number",
    "quote_date",
    "valid_until",
    "currency",
    "delivery_terms",
    "incoterm",
    "lead_time",
)
DATES = ("quote_date", "valid_until")
NEEDED = ("supplier", "quote_number", "quote_date", "currency")
MEDIA = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "eml": "message/rfc822",
}
# The file's lines kept beside the quotation, for the confirmation view to show.
MOST_SOURCE_LINES = 600
LENGTHS = {
    "supplier": 200,
    "quote_number": 120,
    "delivery_terms": 200,
    "lead_time": 120,
    "currency": 3,
    "incoterm": 3,
}


class QuotationError(ValueError):
    """A quotation request that cannot be done, with the reason a person can act on."""


def _context(bid: Bid) -> AuditContext:
    return AuditContext(organisation_id=bid.organisation_id, bid_id=bid.id)


def _short(name: str, value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()[: LENGTHS.get(name, 200)] or None
    return value


# --- Capture ----------------------------------------------------------------------------------


def capture(
    session: Session,
    bid: Bid,
    payload: bytes,
    filename: str,
    actor: Actor,
    *,
    store: ObjectStore,
    scanner: Scanner,
    today: date | None = None,
) -> Quotation:
    """Store, scan and read a quotation file, and keep what the rules read as a proposal.
    The same file sent again is the quotation already captured."""
    from firebid.sandbox.runner import SandboxFailure, run_sandboxed

    digest = hashlib.sha256(payload).hexdigest()
    known = session.execute(
        select(Quotation).where(Quotation.bid_id == bid.id, Quotation.sha256 == digest)
    ).scalar_one_or_none()
    if known is not None:
        return known
    kind = reader.kind_of(payload, filename)
    if kind is None:
        raise QuotationError(
            "a quotation is a PDF, an .xlsx workbook or an .eml email; "
            "save an Outlook .msg as .eml and send that"
        )
    key = f"bids/{bid.id}/quotations/{digest}"
    # The bytes may already be kept under their own digest.
    with contextlib.suppress(ObjectExists):
        store.put_once(key, payload, content_type=MEDIA[kind])
    scan = scanner.scan(payload)
    if not scan.may_be_opened:
        log.warning(
            "quotation_not_opened", bid_id=str(bid.id), sha256=digest, verdict=str(scan.verdict)
        )
        if scan.verdict is Verdict.INFECTED:
            raise QuotationError("the file did not pass the malware scan and was not opened")
        raise QuotationError(
            "the malware scanner is not available, so the file was not opened: send it again"
        )
    try:
        found: dict[str, Any] = run_sandboxed(reader.read, payload, kind)
    except SandboxFailure as failure:
        raise QuotationError(f"the file could not be read: {failure.reason}") from failure
    quote = rules.extract(found["lines"], today or datetime.now(UTC).date())
    row = Quotation(
        bid_id=bid.id,
        filename=filename[:300],
        kind=kind,
        sha256=digest,
        storage_key=key,
        scan_signature=scan.signature[:200] or None,
        exclusions=[str(item.value) for item in quote.exclusions],
        extraction={
            "method": "rules",
            "rules_version": rules.RULES_VERSION,
            "fields": {name: read.as_json() for name, read in quote.fields.items()},
            "exclusions": [item.as_json() for item in quote.exclusions],
            "missing": list(quote.missing),
            "source_lines": found["lines"][:MOST_SOURCE_LINES],
        },
        state="extracted",
        created_by_id=actor.id,
    )
    for name in FIELDS:
        setattr(row, name, _short(name, quote.value(name)))
    session.add(row)
    session.flush()
    for line in quote.lines:
        if line.unit_price is None:
            continue
        session.add(
            QuotationLine(
                bid_id=bid.id,
                quotation_id=row.id,
                ordinal=line.ordinal,
                description=line.description,
                brand=_short("brand", line.brand),
                model=_short("model", line.model),
                unit=(line.unit or "")[:16] or None,
                unit_price=line.unit_price,
                moq=(line.moq or "")[:60] or None,
                lead_time=_short("lead_time", line.lead_time),
                source=dict(line.source),
            )
        )
    session.flush()
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="quotation: captured",
        entity_type=Quotation.__tablename__,
        entity_id=row.id,
        after={
            "file": filename,
            "sha256": digest,
            "supplier": row.supplier,
            "quote_number": row.quote_number,
            "lines": len(quote.lines),
            "missing": list(quote.missing),
        },
    )
    return row


def quotations(session: Session, bid_id: uuid.UUID) -> list[Quotation]:
    return list(
        session.execute(
            select(Quotation).where(Quotation.bid_id == bid_id).order_by(Quotation.created_at)
        ).scalars()
    )


def lines_of(session: Session, quotation: Quotation) -> list[QuotationLine]:
    return list(
        session.execute(
            select(QuotationLine)
            .where(QuotationLine.quotation_id == quotation.id)
            .order_by(QuotationLine.ordinal)
        ).scalars()
    )


def flags_of(bid: Bid, quotation: Quotation, today: date | None = None) -> list[rules.Flag]:
    """What a person must know before using the quotation (FR-CST-03)."""
    return rules.flags(
        quotation.valid_until,
        list(quotation.exclusions or []),
        today or datetime.now(UTC).date(),
        tender_validity_end(bid.submission_deadline, bid.tender_validity_days),
    )


def _missing(quotation: Quotation, lines: int) -> list[str]:
    missing = [name for name in rules.REQUIRED if getattr(quotation, name) in (None, "")]
    return [*missing, "lines"] if lines == 0 else missing


def _open(quotation: Quotation) -> None:
    if quotation.state != "extracted":
        raise QuotationError(f"the quotation is already {quotation.state}")


# --- What a person corrects -------------------------------------------------------------------


def correct(
    session: Session, bid: Bid, quotation: Quotation, values: dict[str, Any], actor: Actor
) -> Quotation:
    """Fields as a person read them from the file, in place of what was proposed."""
    _open(quotation)
    extraction = dict(quotation.extraction or {})
    fields = dict(extraction.get("fields") or {})
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for name, value in values.items():
        if name == "exclusions":
            wanted = [str(item).strip() for item in value or [] if str(item).strip()]
            before[name], after[name] = list(quotation.exclusions or []), wanted
            quotation.exclusions = wanted
            extraction["exclusions"] = [
                {"value": item, "source": None, "method": "person", "by": actor.label}
                for item in wanted
            ]
            continue
        if name not in FIELDS:
            raise QuotationError(f"{name!r} is not a field of a quotation")
        if name in DATES and isinstance(value, str):
            value = rules.parse_date(value)
            if value is None:
                raise QuotationError(f"{name.replace('_', ' ')} is a date")
        if name == "currency" and value is not None:
            value = str(value).strip().upper()
            if not re.fullmatch(r"[A-Z]{3}", value):
                raise QuotationError("a currency is its three-letter code, such as USD")
        if name == "incoterm" and value:
            value = str(value).strip().upper()
            if value not in rules.INCOTERMS:
                raise QuotationError(f"{value} is not an Incoterm")
        value = _short(name, value)
        old = getattr(quotation, name)
        before[name] = old.isoformat() if isinstance(old, date) else old
        after[name] = value.isoformat() if isinstance(value, date) else value
        setattr(quotation, name, value)
        fields[name] = {"value": after[name], "source": None, "method": "person", "by": actor.label}
    extraction["fields"] = fields
    extraction["missing"] = _missing(quotation, len(lines_of(session, quotation)))
    quotation.extraction = extraction
    session.flush()
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="quotation: corrected",
        entity_type=Quotation.__tablename__,
        entity_id=quotation.id,
        before=before,
        after=after,
    )
    return quotation


def link_line(
    session: Session,
    bid: Bid,
    line: QuotationLine,
    actor: Actor,
    *,
    item_key: str | None = None,
    boq_line_id: uuid.UUID | None = None,
    unit: str | None = None,
    unit_price: Decimal | None = None,
) -> QuotationLine:
    """Say what a quotation line prices: a rate-library item key, a BOQ line, or both. A BOQ
    line gives its own item key. With neither, the link is cleared."""
    quotation = session.get(Quotation, line.quotation_id)
    if quotation is None or quotation.bid_id != bid.id:
        raise QuotationError("no such quotation line")
    _open(quotation)
    target: BoqLine | None = None
    if boq_line_id is not None:
        target = session.get(BoqLine, boq_line_id)
        if target is None or target.bid_id != bid.id:
            raise QuotationError("no such BOQ line in this bid")
        if not item_key:
            item_key = target.item_key
        if not item_key:
            raise QuotationError(
                "the BOQ line has no item key: give the rate-library item key it is priced under"
            )
    if item_key:
        key = ItemKey.parse(item_key.strip().lower())
        if not key.type:
            raise QuotationError("an item key starts with the type of item")
        item_key = key.text()
    if unit is not None:
        if unit_of(unit) is None:
            raise QuotationError(f"{unit!r} is not a unit a rate is quoted in")
        line.unit = unit.strip().lower()[:16]
    elif line.unit is None and target is not None:
        line.unit = target.unit
    if unit_price is not None:
        if unit_price <= 0:
            raise QuotationError("a unit price is more than zero")
        line.unit_price = unit_price
    line.item_key = item_key or None
    line.boq_line_id = target.id if target is not None else None
    session.flush()
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="quotation line: linked" if line.item_key else "quotation line: unlinked",
        entity_type=QuotationLine.__tablename__,
        entity_id=line.id,
        after={
            "item_key": line.item_key,
            "boq_line_id": str(line.boq_line_id) if line.boq_line_id else None,
            "unit": line.unit,
            "unit_price": str(line.unit_price),
        },
    )
    return line


# --- The model, for what the rules could not read ---------------------------------------------


def _said_on(value: str, cells: str) -> bool:
    """Whether a line of the file bears a value out: its words, or its figures, are there."""
    squashed = re.sub(r"[\s,]+", "", cells.lower())
    return re.sub(r"[\s,]+", "", value.lower()) in squashed


def read_with_model(
    session: Session, bid: Bid, quotation: Quotation, router: Any, actor: Actor
) -> Quotation:
    """Ask the model for the fields (and, where none were read, the lines) the rules could
    not read. An answer is kept only where the line it cites bears it out, and never
    replaces what the rules or a person read."""
    from firebid.agents.base import AgentInput
    from firebid.agents.quotation_reader import QuotationAnswer, QuotationInput, QuotationReader
    from firebid.agents.runtime import Escalated, run_agent_with_result

    _open(quotation)
    extraction = dict(quotation.extraction or {})
    source: list[dict[str, Any]] = list(extraction.get("source_lines") or [])
    existing = lines_of(session, quotation)
    wanted = [name for name in _missing(quotation, len(existing)) if name != "incoterm"]
    for name in ("delivery_terms", "lead_time"):
        if getattr(quotation, name) is None:
            wanted.append(name)
    if not source or not wanted:
        return quotation
    texts = [" | ".join(line["cells"]) for line in source]
    request = AgentInput(
        bid_id=bid.id,
        idempotency_key=f"quotation:{quotation.id}:"
        + hashlib.sha256(",".join(wanted).encode()).hexdigest()[:24],
        payload=QuotationInput(lines=texts, wanted=wanted),
        actor_label=actor.label,
    )
    try:
        run, result = run_agent_with_result(session, QuotationReader(router), request)
    except Escalated:
        return quotation
    if result is None or not isinstance(result.output, QuotationAnswer):
        return quotation
    fields = dict(extraction.get("fields") or {})
    dropped: list[dict[str, Any]] = []
    kept: list[str] = []

    def where(index: int) -> dict[str, Any] | None:
        if not 0 <= index < len(source):
            return None
        return rules.source_of(source[index])

    for answer in result.output.fields:
        place = where(answer.line)
        value: Any = answer.value.strip()
        if answer.name in DATES:
            value = rules.parse_date(answer.value)
        elif answer.name == "currency":
            value = rules.currency_in(answer.value)
        # A date or currency is checked as the file writes it; the rest word for word.
        shown = (
            place is not None
            and value not in (None, "")
            and (
                answer.name in DATES
                or answer.name == "currency"
                or _said_on(answer.value, texts[answer.line])
            )
        )
        if answer.name in DATES and shown:
            shown = isinstance(value, date) and _date_on(value, texts[answer.line])
        if answer.name == "currency" and shown:
            shown = rules.currency_in(texts[answer.line]) == value
        if not shown:
            dropped.append({"name": answer.name, "value": answer.value, "line": answer.line})
            continue
        read = {
            "value": value.isoformat() if isinstance(value, date) else value,
            "source": place,
            "method": "model",
            "confidence": answer.confidence,
        }
        if answer.name == "exclusion":
            if value not in (quotation.exclusions or []):
                quotation.exclusions = [*(quotation.exclusions or []), value]
                extraction["exclusions"] = [*(extraction.get("exclusions") or []), read]
                kept.append("exclusion")
            continue
        if answer.name not in wanted or getattr(quotation, answer.name) not in (None, ""):
            continue
        setattr(quotation, answer.name, _short(answer.name, value))
        fields[answer.name] = read
        kept.append(answer.name)
    if "lines" in wanted:
        count = len(existing)
        for item in result.output.lines:
            place = where(item.line)
            price = rules.parse_money(item.unit_price)
            # The price is the figure written on the cited line, or it is not a price.
            if (
                place is None
                or price is None
                or price <= 0
                or not _said_on(item.unit_price, texts[item.line])
                or not item.description.strip()
            ):
                dropped.append({"name": "line", "value": item.description, "line": item.line})
                continue
            session.add(
                QuotationLine(
                    bid_id=bid.id,
                    quotation_id=quotation.id,
                    ordinal=count + 1,
                    description=item.description.strip(),
                    brand=_short("brand", item.brand),
                    model=_short("model", item.model),
                    unit=(item.unit or "").strip().lower()[:16] or None,
                    unit_price=price,
                    moq=(item.moq or "")[:60] or None,
                    lead_time=_short("lead_time", item.lead_time),
                    source={**place, "method": "model", "confidence": item.confidence},
                )
            )
            count += 1
            kept.append("line")
        session.flush()
    if quotation.incoterm is None and quotation.delivery_terms:
        terms = quotation.delivery_terms.upper()
        quotation.incoterm = next(
            (t for t in rules.INCOTERMS if re.search(rf"\b{t}\b", terms)), None
        )
    extraction["fields"] = fields
    extraction["missing"] = _missing(quotation, len(lines_of(session, quotation)))
    extraction["model"] = {
        "provider": run.provider,
        "model": run.model,
        "prompt_version": run.prompt_version,
        "agent_run_id": str(run.id),
        "kept": kept,
        "dropped": dropped,
    }
    quotation.extraction = extraction
    session.flush()
    return quotation


DATE_WORDS = re.compile(
    r"\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}"
    r"|[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}"
)


def _date_on(day: date, text: str) -> bool:
    """Whether a line of the file writes the date, in any of the ways a date is written."""
    return any(rules.parse_date(found) == day for found in DATE_WORDS.findall(text))


# --- Confirming: the quotation becomes rates --------------------------------------------------


def fx_rates(session: Session, organisation_id: uuid.UUID) -> list[landing.FxRate]:
    return [
        landing.FxRate(row.currency, row.rate, row.source, row.as_of)
        for row in session.execute(
            select(FxRate).where(FxRate.organisation_id == organisation_id)
        ).scalars()
    ]


def landed_cost(
    session: Session, bid: Bid, quotation: Quotation, line: QuotationLine, today: date
) -> landing.Landed:
    """A line's unit cost in SGD, by the latest FX rate recorded on or before the day."""
    currency = (quotation.currency or "").upper()
    fx = landing.latest_rate(fx_rates(session, bid.organisation_id), currency, today)
    try:
        return landing.landed(
            line.unit_price,
            currency,
            fx=fx,
            delivery_terms=quotation.incoterm,
        )
    except landing.NoFxRate as refusal:
        raise QuotationError(str(refusal)) from refusal


def confirm(
    session: Session, bid: Bid, quotation: Quotation, actor: Actor, today: date | None = None
) -> Quotation:
    """A named person confirms the quotation as read. Each linked line becomes a rate
    library entry at its landed cost, and prices the BOQ line it was linked to."""
    from firebid.services import pricing

    _open(quotation)
    if actor.id is None:
        raise QuotationError("a quotation is confirmed by a named person")
    today = today or datetime.now(UTC).date()
    wanting = [name for name in NEEDED if getattr(quotation, name) in (None, "")]
    if wanting:
        raise QuotationError(
            "the quotation does not state its " + ", ".join(n.replace("_", " ") for n in wanting)
        )
    linked = [line for line in lines_of(session, quotation) if line.item_key]
    if not linked:
        raise QuotationError("link at least one line to an item key or a BOQ line first")
    seen: set[tuple[str, str]] = set()
    for line in linked:
        unit = unit_of(line.unit)
        if unit is None:
            raise QuotationError(f"line {line.ordinal} has no unit a rate is quoted in")
        if (str(line.item_key), unit) in seen:
            raise QuotationError(
                f"line {line.ordinal} prices an item another line of the quotation prices"
            )
        seen.add((str(line.item_key), unit))
    reference = f"{quotation.supplier} {quotation.quote_number}"[:200]
    current = {
        (rate.item_key, rate.unit): rate
        for rate in session.execute(
            select(Rate).where(
                Rate.organisation_id == bid.organisation_id,
                Rate.source_type == "quotation",
                Rate.source_reference == reference,
                Rate.retired_at.is_(None),
            )
        ).scalars()
    }
    now = datetime.now(UTC)
    made = []
    for line in linked:
        unit = str(unit_of(line.unit))
        cost = landed_cost(session, bid, quotation, line, today)
        old = current.get((str(line.item_key), unit))
        if old is not None:
            old.retired_at = now
            session.flush()  # the current-version index allows one current row
        rate = Rate(
            organisation_id=bid.organisation_id,
            item_key=str(line.item_key),
            key_parts=ItemKey.parse(str(line.item_key)).parts(),
            description=" ".join(
                part for part in (line.description, line.brand, line.model) if part
            ),
            unit=unit,
            unit_rate=cost.unit_cost,
            currency="SGD",
            source_type="quotation",
            source_reference=reference,
            effective_from=quotation.quote_date,
            valid_until=quotation.valid_until,
            version=(old.version + 1) if old else 1,
            supersedes_id=old.id if old else None,
            created_by_id=actor.id,
            quotation_line_id=line.id,
            landed=cost.as_json(),
        )
        session.add(rate)
        session.flush()
        line.rate_id = rate.id
        line.landed = cost.as_json()
        made.append(rate)
        if line.boq_line_id is not None:
            target = session.get(BoqLine, line.boq_line_id)
            if target is not None:
                try:
                    pricing.price_line(
                        session,
                        target,
                        rate,
                        method="person",
                        actor=actor,
                        reason=f"quotation {reference}, line {line.ordinal}",
                        provenance={
                            "quotation_id": str(quotation.id),
                            "quotation_line_id": str(line.id),
                        },
                    )
                except pricing.PricingError as refusal:
                    raise QuotationError(f"line {line.ordinal}: {refusal}") from refusal
    quotation.state = "confirmed"
    quotation.decided_by = actor.label
    quotation.decided_by_id = actor.id
    quotation.decided_at = now
    session.flush()
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="quotation: confirmed",
        entity_type=Quotation.__tablename__,
        entity_id=quotation.id,
        after={
            "supplier": quotation.supplier,
            "quote_number": quotation.quote_number,
            "currency": quotation.currency,
            "rates": [str(rate.id) for rate in made],
            "flags": [flag.code for flag in flags_of(bid, quotation, today)],
        },
    )
    return quotation


def reject(session: Session, bid: Bid, quotation: Quotation, actor: Actor, note: str) -> Quotation:
    _open(quotation)
    if actor.id is None:
        raise QuotationError("a quotation is rejected by a named person")
    if not note.strip():
        raise QuotationError("say why the quotation is not used")
    quotation.state = "rejected"
    quotation.decided_by = actor.label
    quotation.decided_by_id = actor.id
    quotation.decided_at = datetime.now(UTC)
    quotation.note = note
    session.flush()
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="quotation: rejected",
        entity_type=Quotation.__tablename__,
        entity_id=quotation.id,
        after={"state": "rejected"},
        reason=note,
    )
    return quotation
