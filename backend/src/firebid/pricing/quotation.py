"""What a supplier's quotation says, read from its lines of cells (FR-CST-02, FR-CST-03).

The fields a quotation is captured with:

* the supplier, the quotation's number and date, how long it is valid, and its currency;
* its delivery terms (an Incoterm) and its exclusions;
* its lines: description, brand and model, unit, unit price, minimum order quantity and
  lead time.

Rules read the common layouts: a label and its value side by side ("Quotation No: Q-1042",
"Valid until", "Validity: 30 days"), and a table of lines under a header that names its
columns. What they read is a proposal, each field with the line it was read from, for a
person to check against the document and confirm. Nothing is priced from a quotation until
a person has confirmed it. What the rules cannot read is left for the model (route
`quotation_extract`) or for the person.

A price is read exactly as written: a figure, in the quotation's currency. The platform
never works one out, fills one in or carries one over from another line (FR-CST-09).

`flags` says what a person must know before using a quotation: it has expired, it expires
before the tender's own validity ends, or it excludes things.

Pure: lines of cells in, a proposed quotation out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from typing import Any

RULES_VERSION = "quotation-rules-1"

CURRENCIES = ("SGD", "USD", "EUR", "GBP", "CNY", "RMB", "MYR", "JPY", "AUD")
SYMBOLS = (("S$", "SGD"), ("US$", "USD"), ("€", "EUR"), ("£", "GBP"), ("RM", "MYR"))
INCOTERMS = ("EXW", "FCA", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP")

LABELS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("supplier", re.compile(r"^(supplier|vendor|from|company)\s*:?$", re.I)),
    (
        "quote_number",
        re.compile(
            r"^(quotation|quote)\s*(no\.?|number|ref\.?|reference|#)\s*:?$|^our\s+ref\.?\s*:?$",
            re.I,
        ),
    ),
    ("quote_date", re.compile(r"^(quotation\s+|quote\s+)?date\s*:?$", re.I)),
    (
        "valid_until",
        re.compile(
            r"^(valid\s+(until|till|to)|validity(\s+date)?|expiry(\s+date)?|price\s+validity)\s*:?$",
            re.I,
        ),
    ),
    ("currency", re.compile(r"^currency\s*:?$", re.I)),
    (
        "delivery_terms",
        re.compile(
            r"^(delivery\s+terms?|incoterms?|terms\s+of\s+delivery|price\s+basis)\s*:?$", re.I
        ),
    ),
    ("lead_time", re.compile(r"^(lead\s*time|delivery(\s+time|\s+period)?)\s*:?$", re.I)),
    ("exclusions", re.compile(r"^exclusions?\s*:?$", re.I)),
)
COLUMNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("description", re.compile(r"\b(description|item\s+description|product|material)\b", re.I)),
    ("brand", re.compile(r"\b(brand|make|manufacturer)\b", re.I)),
    ("model", re.compile(r"\b(model|part\s*(no\.?|number)|catalogue)\b", re.I)),
    ("unit_price", re.compile(r"\b(unit\s+(price|rate|cost)|rate|price)\b", re.I)),
    ("unit", re.compile(r"^(unit|uom|u/m)$", re.I)),
    ("moq", re.compile(r"\b(moq|min(imum)?\.?\s+(order|qty))\b", re.I)),
    ("lead_time", re.compile(r"\b(lead\s*time|delivery)\b", re.I)),
    ("quantity", re.compile(r"^(qty|quantity)$", re.I)),
    ("number", re.compile(r"^(no\.?|item|s/n|#)$", re.I)),
)
VALIDITY_DAYS = re.compile(r"(\d{1,3})\s*(calendar\s+)?(days?|weeks?|months?)", re.I)
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %B %Y", "%d %b %Y", "%B %d, %Y")
MONEY = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
EXCLUSION_LINE = re.compile(r"^(?:[-•*]|\d+[.)])?\s*(?P<what>.+?)\s*$")
ENDS_TABLE = re.compile(r"^(sub[-\s]?total|total|gst|grand\s+total|terms|notes?|remarks?)\b", re.I)


@dataclass(frozen=True)
class Read:
    """A field as it was read: its value, and the line of the file it was read from."""

    value: Any
    source: dict[str, Any]

    def as_json(self) -> dict[str, Any]:
        value = self.value
        if isinstance(value, date):
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = str(value)
        return {"value": value, "source": self.source}


@dataclass
class QuoteLine:
    ordinal: int
    description: str
    unit_price: Decimal | None
    source: dict[str, Any]
    brand: str | None = None
    model: str | None = None
    unit: str | None = None
    moq: str | None = None
    lead_time: str | None = None


@dataclass
class Quote:
    fields: dict[str, Read] = field(default_factory=dict)
    exclusions: list[Read] = field(default_factory=list)
    lines: list[QuoteLine] = field(default_factory=list)
    # The fields a person must supply because nothing in the file states them.
    missing: list[str] = field(default_factory=list)

    def value(self, name: str) -> Any:
        found = self.fields.get(name)
        return found.value if found else None


REQUIRED = ("supplier", "quote_number", "quote_date", "valid_until", "currency")


def source_of(line: dict[str, Any]) -> dict[str, Any]:
    """Where a line of the file is, and its words: what a field read from it cites."""
    return _source(line)


def _source(line: dict[str, Any]) -> dict[str, Any]:
    return {key: line[key] for key in ("part", "sheet", "page", "row") if key in line} | {
        "text": " | ".join(line["cells"])
    }


def parse_date(text: str) -> date | None:
    cleaned = " ".join(str(text).replace(",", ", ").split()).replace(" ,", ",")
    for candidate in (cleaned, cleaned[:10]):
        for pattern in DATE_FORMATS:
            try:
                return datetime.strptime(candidate, pattern).date()
            except ValueError:
                continue
    return None


def parse_money(text: str) -> Decimal | None:
    """A figure as written, thousands separators removed. Never a guess: no figure, None."""
    match = MONEY.search(str(text))
    if match is None:
        return None
    try:
        value = Decimal(match.group().replace(",", ""))
    except InvalidOperation:
        return None
    return value if value >= 0 else None


def currency_in(text: str) -> str | None:
    upper = str(text).upper()
    found = next((code for code in CURRENCIES if re.search(rf"\b{code}\b", upper)), None)
    if found is not None:
        return "CNY" if found == "RMB" else found
    for symbol, code in SYMBOLS:
        # A symbol stands before a figure and is not the end of a word: "RM 40", not "firm".
        if re.search(rf"(?<![A-Z]){re.escape(symbol.upper())}\s?\d", upper):
            return code
    return None


def _labelled(lines: list[dict[str, Any]]) -> dict[str, tuple[str, dict[str, Any]]]:
    """Each labelled field's words, from a label cell and the cell after it, or from one
    cell written "Label: value". A date is the first of its label that reads as a date: an
    email's own Date header is when it was sent, not the quotation's date."""
    found: dict[str, tuple[str, dict[str, Any]]] = {}
    for line in lines:
        cells = line["cells"]
        pairs = list(pairwise(cells))
        for cell in cells:
            if ":" in cell and not cell.rstrip().endswith(":"):
                label, _, value = cell.partition(":")
                pairs.append((f"{label}:", value.strip()))
        for label, value in pairs:
            for name, pattern in LABELS:
                if name in found or not pattern.match(label.strip()) or not value.strip():
                    continue
                if name == "quote_date" and parse_date(value) is None:
                    continue
                found[name] = (value.strip(), line)
    return found


def _header(cells: list[str]) -> dict[int, str] | None:
    """A table header's columns by position, or None if the line is not one."""
    columns: dict[int, str] = {}
    for position, cell in enumerate(cells):
        name = next((name for name, pattern in COLUMNS if pattern.search(cell.strip())), None)
        if name and name not in columns.values():
            columns[position] = name
    names = set(columns.values())
    return columns if {"description", "unit_price"} <= names else None


def extract(lines: list[dict[str, Any]], today: date | None = None) -> Quote:
    """A quotation proposed from a file's lines of cells."""
    quote = Quote()
    labelled = _labelled(lines)
    for name in ("supplier", "quote_number", "delivery_terms", "lead_time"):
        if name in labelled:
            words, line = labelled[name]
            if name == "supplier":
                # An email's sender is a name and an address: the supplier is the name.
                words = re.sub(r"\s*<[^>]*>\s*", "", words).strip().strip('"') or words
            quote.fields[name] = Read(words, _source(line))
    if "supplier" not in quote.fields:
        # An email says who it is from; a letterhead is the document's first line.
        sender = next(
            (
                line
                for line in lines
                if line.get("part") == "headers" and line["cells"][0] == "From:"
            ),
            None,
        )
        if sender is not None:
            name = re.sub(r"\s*<[^>]*>\s*", "", sender["cells"][1]).strip().strip('"')
            if name:
                quote.fields["supplier"] = Read(name, _source(sender))
    if "quote_date" in labelled:
        words, line = labelled["quote_date"]
        when = parse_date(words)
        if when is not None:
            quote.fields["quote_date"] = Read(when, _source(line))
    if "valid_until" in labelled:
        words, line = labelled["valid_until"]
        until = parse_date(words)
        period = VALIDITY_DAYS.search(words)
        issued = quote.value("quote_date")
        if until is None and period is not None and isinstance(issued, date):
            count, unit = int(period.group(1)), period.group(3).lower()
            days = count * (30 if unit.startswith("month") else 7 if unit.startswith("week") else 1)
            until = issued + timedelta(days=days)
        if until is not None:
            quote.fields["valid_until"] = Read(until, _source(line))
    terms = quote.value("delivery_terms")
    if isinstance(terms, str):
        incoterm = next((t for t in INCOTERMS if re.search(rf"\b{t}\b", terms.upper())), None)
        if incoterm:
            quote.fields["incoterm"] = Read(incoterm, quote.fields["delivery_terms"].source)

    header_at = next((i for i, line in enumerate(lines) if _header(line["cells"])), None)
    if "currency" in labelled:
        words, line = labelled["currency"]
        code = currency_in(words)
        if code:
            quote.fields["currency"] = Read(code, _source(line))
    if "currency" not in quote.fields and header_at is not None:
        # "Unit price (USD)" in the header states the currency of the prices under it.
        code = currency_in(" ".join(lines[header_at]["cells"]))
        if code:
            quote.fields["currency"] = Read(code, _source(lines[header_at]))

    if header_at is not None:
        columns = _header(lines[header_at]["cells"]) or {}
        part = lines[header_at].get("part")
        for line in lines[header_at + 1 :]:
            cells = line["cells"]
            if line.get("part") != part or not cells or ENDS_TABLE.match(cells[0]):
                break
            values = {
                name: cells[position] for position, name in columns.items() if position < len(cells)
            }
            description = values.get("description", "").strip()
            price = parse_money(values.get("unit_price", ""))
            if not description or price is None:
                continue  # a heading inside the table, or a line with no price: not a line
            quote.lines.append(
                QuoteLine(
                    ordinal=len(quote.lines) + 1,
                    description=description,
                    unit_price=price,
                    source=_source(line),
                    brand=values.get("brand") or None,
                    model=values.get("model") or None,
                    unit=(values.get("unit") or "").lower() or None,
                    moq=values.get("moq") or None,
                    lead_time=values.get("lead_time") or None,
                )
            )

    if "exclusions" in labelled:
        words, line = labelled["exclusions"]
        quote.exclusions.append(Read(words, _source(line)))
    for index, line in enumerate(lines):
        if (
            re.match(r"^exclusions?\s*:?$", line["cells"][0].strip(), re.I)
            and len(line["cells"]) == 1
        ):
            previous = line.get("row")
            for following in lines[index + 1 :]:
                first = following["cells"][0].strip()
                if following.get("part") != line.get("part"):
                    break
                # A blank line ends the list: the lines after it are something else.
                row = following.get("row")
                if previous is not None and row is not None and row != previous + 1:
                    break
                previous = row
                if re.match(r"^[A-Z][\w\s]{2,30}:$", first) or ENDS_TABLE.match(first):
                    break
                if len(quote.exclusions) >= 20:
                    break
                what = EXCLUSION_LINE.match(" ".join(following["cells"]))
                if what and what.group("what"):
                    quote.exclusions.append(Read(what.group("what"), _source(following)))
    quote.missing = [name for name in REQUIRED if name not in quote.fields]
    if not quote.lines:
        quote.missing.append("lines")
    return quote


# --- What a person must know before using it (FR-CST-03) --------------------------------------


@dataclass(frozen=True)
class Flag:
    code: str  # expired | ends_before_tender | exclusions | no_validity
    message: str

    def as_json(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def flags(
    valid_until: date | None,
    exclusions: list[str],
    today: date,
    tender_valid_until: date | None,
) -> list[Flag]:
    found = []
    if valid_until is None:
        found.append(Flag("no_validity", "the quotation does not say how long it is valid"))
    elif valid_until < today:
        found.append(Flag("expired", f"the quotation expired on {valid_until.isoformat()}"))
    elif tender_valid_until is not None and valid_until < tender_valid_until:
        found.append(
            Flag(
                "ends_before_tender",
                f"the quotation is valid until {valid_until.isoformat()}, before the tender's "
                f"validity ends on {tender_valid_until.isoformat()}",
            )
        )
    if exclusions:
        found.append(Flag("exclusions", f"the quotation excludes: {'; '.join(exclusions)}"))
    return found
