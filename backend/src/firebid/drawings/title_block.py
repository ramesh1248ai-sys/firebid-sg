"""Reading a drawing's title block from text with positions (FR-DOC-02).

Deterministic first. The text comes from wherever the sheet keeps it: a PDF's text layer, a
DXF's TEXT entities, or OCR of the rendered corner. This module never opens a file; it gets
spans with boxes and returns fields with a confidence each. Only when that confidence is too
low does anyone ask a model, and then the model's answer is a proposal like this one.

How it reads, in order:

1. **Find the block.** Consultants put it in the bottom-right corner, down the right edge or
   along the bottom. Each candidate region is scored by the labels and drawing numbers in it,
   and the best one wins.
2. **Set the revision history aside.** Most title blocks carry a table of every issue. Its
   rows hold revision labels too, and the newest is not always the one at the top, so a
   reader that takes "the revision nearest a REV label" is right until the day it is not. The
   table is recognised by its header row (REV beside DATE or DESCRIPTION) and kept out.
3. **Pair labels with values by geometry.** A value sits below its label in the same cell, or
   beside it on the same line; `LABEL: value` in one span is split.
4. **Validate each value against its pattern.** A drawing number that does not look like one
   is kept, at a low confidence, rather than trusted or dropped.

Coordinates are in any unit, with y growing downwards; everything is measured relative to text
height or the page, so PDF millimetres, DXF drawing units and OCR pixels all work.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import StrEnum

# Confidence for a value read from a labelled cell and matching its pattern. High, because
# both the layout and the content agree; not 1.0, because a label can sit over the wrong cell.
LABELLED = 0.97
# A labelled value that does not match its pattern: probably misread.
LABELLED_BUT_ODD = 0.55
# A value found by its pattern alone, with no label pointing at it.
PATTERN_ONLY = 0.75
# A value taken from a remembered layout for this consultant.
TEMPLATE = 0.98

# Below this, the sheet's critical fields go to the model and then to a person.
DEFAULT_THRESHOLD = 0.85


class Field(StrEnum):
    SHEET_NUMBER = "sheet_number"
    TITLE = "title"
    REVISION = "revision"
    REVISION_DATE = "revision_date"
    SCALE = "scale"
    LEVEL = "level"
    ZONE = "zone"


# The two fields an estimator cannot work without: the wrong number or revision is the wrong
# drawing, and nothing measured on it can be trusted.
CRITICAL = (Field.SHEET_NUMBER, Field.REVISION)

# Label wording seen on Singapore consultants' title blocks. Matched against the whole span,
# after trailing punctuation is removed.
LABELS: dict[Field, re.Pattern[str]] = {
    Field.SHEET_NUMBER: re.compile(
        r"(DRAWING|DWG|DRG)\s*(NO|NUMBER|NUM|#)|SHEET\s*(NO|NUMBER)|DRAWING\s*REF", re.I
    ),
    Field.TITLE: re.compile(r"(DRAWING\s+|SHEET\s+)?TITLE", re.I),
    Field.REVISION: re.compile(r"REV(ISION)?(\s*NO)?", re.I),
    Field.REVISION_DATE: re.compile(r"(ISSUE\s+)?DATE(\s+OF\s+ISSUE)?", re.I),
    Field.SCALE: re.compile(r"SCALE(\s*@\s*A\d)?", re.I),
    Field.LEVEL: re.compile(r"LEVEL|STOREY|FLOOR", re.I),
    Field.ZONE: re.compile(r"ZONE|AREA", re.I),
}
# Headings that mark a revision history table when they share a line with a REV label.
# DATE alone is weak evidence: a title block's bottom row often has REV, SCALE and DATE cells
# side by side, and that row is not a history table.
HISTORY_HEADINGS = re.compile(
    r"DESCRIPTION|AMENDMENTS?|DETAILS|REMARKS|CHK'?D|CHECKED|APP'?D|APPROVED|BY", re.I
)
WEAK_HISTORY_HEADING = re.compile(r"DATE", re.I)

# Labels of cells that are not fields here. They are never taken as another label's value.
OTHER_LABELS = re.compile(
    r"PROJECT|CLIENT|CONSULTANTS?|ARCHITECT|ENGINEER|DRAWN(\s+BY)?|CHECKED(\s+BY)?|"
    r"APPROVED(\s+BY)?|DESIGNED(\s+BY)?|JOB\s*NO|PROJECT\s*NO|STATUS|NORTH|KEY\s*PLAN|NOTES|"
    r"STAMP|QP|PE\s+ENDORSEMENT",
    re.I,
)

# A drawing number: a letter prefix (`FP-L05-201`), or a project number with an optional
# bracketed building or discipline code (`6405(HFC)-F/1B`), then one to eight parts: a
# number built to a naming standard (`60743399_ACM_TN_TO_D_MFP_A03-10-01`) runs to seven. A
# date (`2026-09-26`, `26/09/2026`) has the same shape and is never one; nor is a telephone
# number, whose second part is longer than any part of a drawing number.
DRAWING_NUMBER = re.compile(
    r"(?!\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}$)"
    r"(?:[A-Z]{1,5}\d{0,3}|\d{2,10}(?:\([A-Z0-9]{1,6}\))?)(?:[-_/.][A-Z0-9]{1,6}){1,8}"
)
# A revision code, or a dash: a first issue, not revised yet, as tender sets often mark it.
REVISION = re.compile(r"[A-Z]{0,2}\d{1,3}|[A-Z]{1,2}|[-\u2013]")
SCALE = re.compile(
    r"(1\s*:\s*\d{1,5}(\s*@\s*A\d)?)|N\.?T\.?S\.?|NOT\s+TO\s+SCALE|AS\s+(SHOWN|INDICATED)", re.I
)
LEVEL_IN_NUMBER = re.compile(r"(?:^|[-_])(L\d{1,2}|B\d{1,2}|RF|GF|UR|MZ)(?=[-_]|$)")
ZONE_IN_NUMBER = re.compile(r"(?:^|[-_])(Z[A-Z0-9]{1,2})(?=[-_]|$)")

# Discipline from the drawing number's prefix. Longest prefix wins, so `FPS` beats `FP`.
DISCIPLINES: dict[str, str] = {
    "FP": "fire protection",
    "FPS": "fire protection",
    "SP": "fire protection",
    "FS": "fire protection",
    "FA": "fire alarm",
    "ACMV": "mechanical",
    "M": "mechanical",
    "ME": "mechanical",
    "E": "electrical",
    "EL": "electrical",
    "P": "plumbing",
    "PS": "plumbing",
    "A": "architectural",
    "AR": "architectural",
    "S": "structural",
    "ST": "structural",
    "C": "civil",
}

DATE_FORMATS = (
    "%d.%m.%Y",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%Y-%m-%d",
    "%d.%m.%y",
    "%d/%m/%y",
    "%d %b %Y",
    "%d %B %Y",
    "%d-%b-%Y",
    "%d %b %y",
    # Month and year only (`JUL 2026`), as many Singapore title blocks date an issue: read as
    # the first of the month.
    "%b %Y",
    "%B %Y",
)


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def contains(self, span: Span) -> bool:
        return self.x0 <= span.cx <= self.x1 and self.y0 <= span.cy <= self.y1

    def relative_to(self, page: Box) -> Box:
        """This box as fractions of `page`, so a layout is remembered independent of size."""
        return Box(
            (self.x0 - page.x0) / page.width,
            (self.y0 - page.y0) / page.height,
            (self.x1 - page.x0) / page.width,
            (self.y1 - page.y0) / page.height,
        )

    def absolute_in(self, page: Box) -> Box:
        return Box(
            page.x0 + self.x0 * page.width,
            page.y0 + self.y0 * page.height,
            page.x0 + self.x1 * page.width,
            page.y0 + self.y1 * page.height,
        )

    def padded(self, amount: float) -> Box:
        return Box(self.x0 - amount, self.y0 - amount, self.x1 + amount, self.y1 + amount)


@dataclass(frozen=True)
class Span:
    """One run of text and where it sits. `confidence` is 1.0 unless OCR produced it."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float = 1.0

    @property
    def height(self) -> float:
        return max(self.y1 - self.y0, 1e-9)

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def box(self) -> Box:
        return Box(self.x0, self.y0, self.x1, self.y1)

    @property
    def clean(self) -> str:
        return " ".join(self.text.split())


@dataclass(frozen=True)
class FieldReading:
    """One field: its value, how sure the reading is, and how it was found."""

    value: str | None = None
    confidence: float = 0.0
    how: str = "absent"
    # Where the value was on the page, so a person can be shown it and a layout remembered.
    box: Box | None = None
    # Read by OCR rather than from a text layer: the only readings misreadings are undone in.
    from_ocr: bool = False


@dataclass(frozen=True)
class TitleBlockReading:
    """Everything read from one title block. Absent beats invented: unread fields stay None."""

    region: Box | None
    fields: dict[Field, FieldReading] = field(default_factory=dict)
    # Revision labels listed in the history table, in the order printed.
    history: tuple[str, ...] = ()
    discipline: str | None = None

    def value(self, name: Field) -> str | None:
        return self.fields.get(name, FieldReading()).value

    def confidence_of(self, name: Field) -> float:
        return self.fields.get(name, FieldReading()).confidence

    @property
    def confidence(self) -> float:
        """The weakest critical field: a sheet is only as identified as its worst part."""
        return min(self.confidence_of(name) for name in CRITICAL)

    @property
    def revision_date(self) -> date | None:
        return parse_date(self.value(Field.REVISION_DATE))

    def needs_help(self, threshold: float = DEFAULT_THRESHOLD) -> bool:
        return self.confidence < threshold


def _label_of(span: Span) -> Field | None:
    text = span.clean.rstrip(":.# ").strip()
    for name, pattern in LABELS.items():
        if pattern.fullmatch(text):
            return name
    return None


def _is_label(span: Span) -> bool:
    return _label_of(span) is not None or bool(
        OTHER_LABELS.fullmatch(span.clean.rstrip(":.# ").strip())
    )


def _inline(span: Span) -> tuple[Field, str] | None:
    """`REV: C1`, `DRAWING NO. FP-L05-201`: a label and its value in one span."""
    for name, pattern in LABELS.items():
        match = re.match(rf"^\s*({pattern.pattern})\s*[:.#]?\s+(?P<value>\S.*)$", span.clean, re.I)
        if match:
            return name, match.group("value").strip()
    return None


def _same_line(a: Span, b: Span) -> bool:
    return abs(a.cy - b.cy) < 0.6 * max(a.height, b.height)


# Candidate regions as fractions of the page: bottom-right, right strip, bottom strip.
CANDIDATES = (
    Box(0.55, 0.55, 1.0, 1.0),
    Box(0.75, 0.0, 1.0, 1.0),
    Box(0.0, 0.78, 1.0, 1.0),
)


def locate(spans: Sequence[Span], page: Box) -> Box | None:
    """The region holding the title block, tightened to the text inside it.

    Scored by labels and drawing numbers, which a title block is dense with and a plan is not.
    Ties go to the bottom-right corner, the commonest place.
    """
    best: tuple[float, Box] | None = None
    for candidate in CANDIDATES:
        region = candidate.absolute_in(page)
        inside = [span for span in spans if region.contains(span)]
        score = sum(
            5.0
            if _label_of(span) or _inline(span)
            else 4.0
            if DRAWING_NUMBER.fullmatch(span.clean)
            else 0.2
            for span in inside
        )
        if score > 0 and (best is None or score > best[0]):
            best = (score, region)
    if best is None:
        return None
    region = best[1]
    inside = [span for span in spans if region.contains(span)]
    tallest = max(span.height for span in inside)
    return Box(
        min(span.x0 for span in inside),
        min(span.y0 for span in inside),
        max(span.x1 for span in inside),
        max(span.y1 for span in inside),
    ).padded(tallest)


def _history(spans: Sequence[Span]) -> tuple[set[int], tuple[str, ...]]:
    """Find the revision history table: the spans it holds, and the revisions it lists.

    Its header is a REV label sharing a line with DATE, DESCRIPTION or similar. Its rows are
    the spans below the header, down to the first gap much taller than a row.
    """
    taken: set[int] = set()
    listed: list[str] = []
    for index, span in enumerate(spans):
        if _label_of(span) is not Field.REVISION:
            continue
        line = [other for other in spans if other is not span and _same_line(span, other)]
        headings = [other for other in line if HISTORY_HEADINGS.search(other.clean)]
        other_fields = {_label_of(other) for other in line} - {None, Field.REVISION_DATE}
        if not headings and not other_fields:
            headings = [other for other in line if WEAK_HISTORY_HEADING.fullmatch(other.clean)]
        if not headings:
            continue
        headings += [other for other in line if WEAK_HISTORY_HEADING.fullmatch(other.clean)]
        taken.add(index)
        taken.update(spans.index(heading) for heading in headings)
        left = min([span.x0] + [heading.x0 for heading in headings]) - span.height
        right = max([span.x1] + [heading.x1 for heading in headings]) + 50 * span.height
        below = sorted(
            (
                (other.cy, position)
                for position, other in enumerate(spans)
                if other.cy > span.cy + 0.5 * span.height and left <= other.cx <= right
            ),
        )
        previous = span.cy
        for cy, position in below:
            if cy - previous > 4 * span.height:
                break
            other = spans[position]
            heading = HISTORY_HEADINGS.search(other.clean) or WEAK_HISTORY_HEADING.fullmatch(
                other.clean
            )
            if _label_of(other) and not heading:
                break  # a field label: the table has ended and the title block begun
            taken.add(position)
            previous = cy
            if abs(other.x0 - span.x0) < 2 * span.height:
                # Cleaned exactly as the REV cell is, or the two would disagree over OCR's
                # letter-for-digit swaps rather than over the revision.
                label = normalise(Field.REVISION, other.clean)
                if other.confidence < 1.0:
                    label = ocr_digits(label)
                if REVISION.fullmatch(label):
                    listed.append(label)
    return taken, tuple(listed)


def _value_for(
    label: Span, spans: Sequence[Span], used: set[int], name: Field | None = None
) -> tuple[int, Span] | None:
    """The value belonging to `label`: below it in the same cell, or beside it on its line.

    The nearest candidate that reads as the field's kind of value wins (a date for DATE), so a
    stray mark nearer the label (a (c) sign, a dash) does not. A value set in from the label
    across its cell (`Date:` over `JUL 2026`) is reached only when it reads as one.
    """
    best: tuple[bool, float, int, Span] | None = None
    for index, candidate in enumerate(spans):
        if index in used or candidate is label or _is_label(candidate):
            continue
        # Labels are small and cells are tall: a value 10 label-heights down is normal.
        reach = 14 * label.height
        below = candidate.y0 > label.cy and candidate.cy - label.cy < reach
        aligned = (
            candidate.x0 < label.x1 + 4 * label.height and candidate.x1 > label.x0 - label.height
        )
        set_in = candidate.x0 >= label.x0 and candidate.x0 < label.x1 + 10 * label.height
        beside = _same_line(label, candidate) and 0 <= candidate.x0 - label.x1 < 12 * label.height
        fits = name is not None and _valid(name, normalise(name, candidate.clean))
        # A drawing number under a DATE or SCALE label is the next cell's value, not a date
        # or a scale misread: it is left for the label it belongs to.
        if (
            name is not None
            and name is not Field.SHEET_NUMBER
            and name is not Field.TITLE
            and not fits
            and _valid(Field.SHEET_NUMBER, normalise(Field.SHEET_NUMBER, candidate.clean))
        ):
            continue
        if below and aligned:
            distance = (candidate.cy - label.cy) + 0.5 * abs(candidate.x0 - label.x0)
        elif beside:
            distance = candidate.x0 - label.x1
        elif below and set_in and fits:
            distance = (candidate.cy - label.cy) + 0.5 * abs(candidate.x0 - label.x0)
        else:
            continue
        rank = (not fits, distance)
        if best is None or rank < (best[0], best[1]):
            best = (not fits, distance, index, candidate)
    return None if best is None else (best[2], best[3])


def _valid(name: Field, value: str) -> bool:
    if name is Field.SHEET_NUMBER:
        return bool(DRAWING_NUMBER.fullmatch(value.upper()))
    if name is Field.REVISION:
        return bool(REVISION.fullmatch(value.upper())) and len(value) <= 4
    if name is Field.SCALE:
        return bool(SCALE.fullmatch(value.strip()))
    if name is Field.REVISION_DATE:
        return parse_date(value) is not None
    return bool(value.strip())


def normalise(name: Field, value: str) -> str:
    value = " ".join(value.split())
    if name in (Field.SHEET_NUMBER, Field.REVISION, Field.LEVEL, Field.ZONE):
        # A code never ends in punctuation; OCR often adds a stray comma or full stop.
        return value.upper().strip(" .,;:'\"")
    if name is Field.SCALE:
        compact = value.upper().replace(" ", "")
        if compact.replace(".", "") in ("NTS", "NOTTOSCALE"):
            return "NTS"
        return compact if compact.startswith("1:") else value.upper()
    return value


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = " ".join(value.split()).title()
    for pattern in DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()  # a calendar date
        except ValueError:
            continue
    return None


def discipline_of(sheet_number: str | None) -> str | None:
    if not sheet_number:
        return None
    prefix = re.split(r"[-_/.\d]", sheet_number.upper(), maxsplit=1)[0]
    for length in range(len(prefix), 0, -1):
        if prefix[:length] in DISCIPLINES:
            return DISCIPLINES[prefix[:length]]
    return None


# Two runs on one line closer than this (x their height) are one piece of text a PDF writer
# split, as `6405(HFC)-F/` and `1B` often are; a word space is wider.
JOIN_GAP = 0.3


def joined(spans: Sequence[Span]) -> list[Span]:
    """Runs of text on one line with next to no gap between them, joined into one.

    Left to right, each run is joined onto a run on its line that ends just before it. The
    runs are looked up by where they end, so a sheet of thousands of runs stays quick.
    """
    import bisect

    out: list[Span] = []
    ends: list[tuple[float, int]] = []  # (x1, position in out), kept sorted
    for span in sorted(spans, key=lambda s: s.x0):
        if not span.clean:
            out.append(span)
            continue
        reach = JOIN_GAP * span.height
        low = bisect.bisect_left(ends, (span.x0 - reach * 4, -1))
        high = bisect.bisect_right(ends, (span.x0 + 0.1 * span.height, len(out)))
        target: int | None = None
        for x1, position in ends[low:high]:
            last = out[position]
            if _same_line(last, span) and -0.1 * last.height <= span.x0 - x1 < JOIN_GAP * max(
                last.height, span.height
            ):
                target = position
                break
        if target is None:
            out.append(span)
            bisect.insort(ends, (span.x1, len(out) - 1))
            continue
        last = out[target]
        merged = Span(
            last.text.rstrip() + span.text.lstrip(),
            last.x0,
            min(last.y0, span.y0),
            span.x1,
            max(last.y1, span.y1),
            min(last.confidence, span.confidence),
        )
        ends.remove((last.x1, target))
        out[target] = merged
        bisect.insort(ends, (merged.x1, target))
    return out


def _title_lines(start: tuple[int, Span], spans: Sequence[Span], taken: set[int]) -> str:
    """A title often runs to two lines; take the lines below the first, aligned with it."""
    lines = [start[1]]
    for index, span in sorted(enumerate(spans), key=lambda item: item[1].cy):
        last = lines[-1]
        if index in taken or span is last or _label_of(span) or span.cy <= last.cy:
            continue
        if span.cy - last.cy < 1.8 * last.height and abs(span.x0 - start[1].x0) < last.height:
            lines.append(span)
    return " ".join(line.clean for line in lines)


def read(
    spans: Sequence[Span],
    page: Box,
    *,
    region: Box | None = None,
) -> TitleBlockReading:
    """Read the title block on a page. Deterministic; no model, no network."""
    spans = joined(spans)
    region = region or locate(spans, page)
    if region is None:
        return TitleBlockReading(region=None)
    inside = [span for span in spans if region.contains(span) and span.clean]

    history_spans, history = _history(inside)
    fields: dict[Field, FieldReading] = {}
    used = set(history_spans)

    # Labels first, in reading order, each claiming the nearest value it has not lost to
    # another label. Reading order matters where two labels could reach one value.
    for index, span in sorted(enumerate(inside), key=lambda item: (item[1].cy, item[1].x0)):
        if index in history_spans:
            continue
        inline = _inline(span)
        if inline is not None and _label_of(span) is None:
            name, raw = inline
            if name not in fields:
                value = normalise(name, raw)
                fields[name] = _scored(name, value, "inline", span.box, span.confidence)
                used.add(index)
            continue
        label = _label_of(span)
        if label is None or label in fields:
            continue
        name = label
        found = _value_for(span, inside, used | {index}, name)
        if found is None:
            continue
        position, value_span = found
        used.add(position)
        raw = _title_lines(found, inside, used) if name is Field.TITLE else value_span.clean
        fields[name] = _scored(
            name, normalise(name, raw), "label", value_span.box, value_span.confidence
        )

    # No labelled drawing number: fall back to the tallest thing that looks like one.
    if fields.get(Field.SHEET_NUMBER, FieldReading()).value is None:
        numbers = [
            span
            for index, span in enumerate(inside)
            if index not in used and DRAWING_NUMBER.fullmatch(span.clean.upper())
        ]
        if numbers:
            tallest = max(numbers, key=lambda span: span.height)
            number_text = normalise(Field.SHEET_NUMBER, tallest.clean)
            if tallest.confidence < 1.0:
                number_text = ocr_digits(number_text)
            fields[Field.SHEET_NUMBER] = FieldReading(
                number_text,
                PATTERN_ONLY * tallest.confidence,
                "pattern",
                tallest.box,
                from_ocr=tallest.confidence < 1.0,
            )

    # No REV cell but a history table: its newest row is a guess, and is scored as one.
    if fields.get(Field.REVISION, FieldReading()).value is None and history:
        fields[Field.REVISION] = FieldReading(history[0], 0.5, "history")
    elif history and Field.REVISION in fields:
        fields[Field.REVISION] = _against_history(fields[Field.REVISION], history)

    number = fields.get(Field.SHEET_NUMBER, FieldReading()).value
    for name, pattern in ((Field.LEVEL, LEVEL_IN_NUMBER), (Field.ZONE, ZONE_IN_NUMBER)):
        if name not in fields and number:
            match = pattern.search(number)
            if match:
                fields[name] = FieldReading(match.group(1), 0.9, "drawing number")

    return TitleBlockReading(
        region=region,
        fields=fields,
        history=history,
        discipline=discipline_of(number),
    )


# Letters OCR reads where a digit was printed, and the digit each one stands for.
OCR_DIGITS = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2"}
# O, and I or l, are the confusions common enough to correct; the rest are too often real.
OCR_CORRECTABLE = {"O", "I"}


def ocr_digits(value: str) -> str:
    """Undo OCR's letter-for-digit swaps in a code: `FP-LO5-201` is `FP-L05-201`.

    Only an O or an I sitting next to a digit is changed, so letter codes such as `FP`, `SCH`
    and `LO` are left alone, and only text that came from OCR is ever passed in.
    """
    characters = list(value.upper().replace("l", "1"))
    changed = True
    while changed:
        changed = False
        for index, character in enumerate(characters):
            if character not in OCR_CORRECTABLE:
                continue
            neighbours = characters[max(index - 1, 0) : index] + characters[index + 1 : index + 2]
            if any(neighbour.isdigit() for neighbour in neighbours):
                characters[index] = OCR_DIGITS[character]
                changed = True
    return "".join(characters)


# Characters OCR confuses with each other, both ways. Used only to reconcile a REV cell with
# the revision history on the same title block, never to invent a value.
OCR_CONFUSABLE: dict[str, str] = {
    "1": "ILl|",
    "I": "1L",
    "L": "1I",
    "0": "OQD",
    "O": "0QD",
    "D": "0O",
    "Q": "0O",
    "5": "S",
    "S": "5",
    "2": "Z",
    "Z": "2",
    "8": "B",
    "B": "8",
    "C": "G",
    "G": "C",
}
# A revision the title block's own history does not list is doubtful; this is its ceiling.
NOT_IN_HISTORY = 0.6


def _variants(value: str, edits: int = 2) -> set[str]:
    found = {value}
    frontier = {value}
    for _ in range(edits):
        grown = set()
        for text in frontier:
            for index, character in enumerate(text):
                for replacement in OCR_CONFUSABLE.get(character, ""):
                    grown.add(text[:index] + replacement.upper() + text[index + 1 :])
        found |= grown
        frontier = grown
    return found


def _against_history(found: FieldReading, history: tuple[str, ...]) -> FieldReading:
    """Check the REV cell against the revision history on the same title block.

    The cell normally names a revision the history lists. From OCR, a value the history
    lacks but a near variant it has (C1 read as CL, C5 as G5) is the variant: two readings of
    the same label in two places agree. Otherwise, from OCR or a text layer alike, a revision
    missing from its own history is a question for someone, not an answer.
    """
    value = found.value
    if value is None or value in history:
        return found
    if found.from_ocr:
        candidates = _variants(value) & set(history)
        if len(candidates) == 1:
            corrected = next(iter(candidates))
            return FieldReading(
                corrected,
                round(found.confidence * 0.95, 4),
                f"{found.how}+history",
                found.box,
                from_ocr=True,
            )
    return FieldReading(
        value,
        min(found.confidence, NOT_IN_HISTORY),
        f"{found.how}, not in history",
        found.box,
        from_ocr=found.from_ocr,
    )


def _scored(name: Field, value: str, how: str, box: Box, text_confidence: float) -> FieldReading:
    if text_confidence < 1.0 and name in (Field.SHEET_NUMBER, Field.REVISION):
        value = ocr_digits(value)
    base = (
        (LABELLED if how == "label" else LABELLED - 0.02)
        if _valid(name, value)
        else (LABELLED_BUT_ODD)
    )
    return FieldReading(
        value, round(base * text_confidence, 4), how, box, from_ocr=text_confidence < 1.0
    )


# --- Remembered layouts --------------------------------------------------------------------


@dataclass(frozen=True)
class Layout:
    """Where one consultant puts each field, as fractions of the page.

    Learnt from a title block a person has confirmed; after that, the same consultant's
    sheets are read by position, with no search and no model.
    """

    region: Box
    fields: dict[Field, Box]

    def to_json(self) -> dict[str, object]:
        def dump(box: Box) -> list[float]:
            return [round(box.x0, 5), round(box.y0, 5), round(box.x1, 5), round(box.y1, 5)]

        return {
            "region": dump(self.region),
            "fields": {str(name): dump(box) for name, box in self.fields.items()},
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> Layout:
        def load(values: object) -> Box:
            if not isinstance(values, list) or len(values) != 4:
                raise ValueError("a layout box is four numbers")
            return Box(*(float(value) for value in values))

        raw_fields = data.get("fields")
        if not isinstance(raw_fields, dict):
            raise ValueError("a layout needs its fields")
        return cls(
            region=load(data.get("region")),
            fields={Field(name): load(box) for name, box in raw_fields.items()},
        )


def learn(reading: TitleBlockReading, page: Box) -> Layout:
    """Remember where a confirmed reading found each field."""
    if reading.region is None:
        raise ValueError("a reading with no title block has no layout to learn")
    fields = {
        name: found.box.relative_to(page)
        for name, found in reading.fields.items()
        if found.box is not None and found.value is not None
    }
    return Layout(region=reading.region.relative_to(page), fields=fields)


def read_with_layout(spans: Sequence[Span], page: Box, layout: Layout) -> TitleBlockReading:
    """Read by position from a remembered layout; fall back to searching for missed fields."""
    fields: dict[Field, FieldReading] = {}
    for name, relative in layout.fields.items():
        area = relative.absolute_in(page)
        # Grow the box by a text height, since the same field on another sheet is rarely
        # the same width: FP-L05-201 and FP-B1-12 do not end in the same place.
        hits = [
            span
            for span in spans
            if area.padded(max(area.height, 1e-9)).contains(span)
            and span.clean
            and not _label_of(span)
        ]
        if not hits:
            continue
        hits.sort(key=lambda span: (span.cy, span.x0))
        value = normalise(name, " ".join(span.clean for span in hits))
        confidence = min(span.confidence for span in hits)
        valid = _valid(name, value)
        fields[name] = FieldReading(
            value,
            round((TEMPLATE if valid else LABELLED_BUT_ODD) * confidence, 4),
            "layout",
            area,
        )

    searched = read(spans, page, region=layout.region.absolute_in(page))
    for name, found in searched.fields.items():
        if name not in fields or fields[name].confidence < found.confidence:
            fields[name] = found
    number = fields.get(Field.SHEET_NUMBER, FieldReading()).value
    return replace(searched, fields=fields, discipline=discipline_of(number))


def fingerprint(spans: Iterable[Span], page: Box) -> frozenset[tuple[str, int, int]]:
    """The labels on a page and roughly where they are: what makes a layout recognisable.

    The revision history is left out: it grows by a row with every issue, so its header moves
    even though the consultant's layout has not.
    """
    spans = list(spans)
    in_history, _ = _history(spans)
    marks = set()
    for index, span in enumerate(spans):
        if index in in_history:
            continue
        name = _label_of(span)
        if name is not None:
            relative = span.box.relative_to(page)
            marks.add((str(name), round(relative.x0 * 20), round(relative.y0 * 20)))
    return frozenset(marks)


def similarity(a: frozenset[tuple[str, int, int]], b: frozenset[tuple[str, int, int]]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
