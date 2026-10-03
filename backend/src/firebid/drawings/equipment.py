"""Equipment as drawings state it: tags, schedules, level schedules, systems (FR-VIS-04).

A pump room layout or a riser schematic shows equipment as a symbol with a tag beside it
("FP-01"), and says what it is in a schedule: a table with a row per tag giving its duty,
flow and head. This module reads those words; the symbols themselves are found as every
other symbol is (P1-04, P1-05).

* **Tags:** the tag written nearest each equipment symbol, each tag given to one symbol.
* **Schedules:** a heading with SCHEDULE in it, a header row naming the columns, and a row
  per tag beneath. Columns are known by their headings, and a flow is brought to L/min
  whatever unit the heading states. Other words that happen to lie among the rows (a grid
  bubble, a legend across the sheet) are not of the table and are passed over.
* **Level schedule:** the levels a schematic or section names with their finished floor
  levels ("L05 FFL +18.000"), which give each level's floor-to-floor height for the riser
  rule (P1-07), and say which level something drawn on the schematic is on.
* **Systems:** which fire protection system a connected piece of pipework belongs to, from
  what stands on it: hydrants, hose reels, landing valves, sprinklers, or a riser named as
  a wet or dry rising main.

Every value keeps the words it was read from. Nothing here is a quantity: a schedule's own
QTY column is kept as written and never counted from.

Pure: text spans and symbols in, what they say out.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import pairwise
from typing import Any

# A tag: a few letters, a separator, a number ("FP-01", "JP 1", "HR-3A"). Pipe sizes, levels
# and grid references are letters and digits too, and are not tags.
TAG = re.compile(r"^([A-Z]{2,5})[-/ ]?(\d{1,3}[A-Z]?)$")
NOT_TAG_PREFIXES = ("DN", "NB", "RF", "FFL", "SSL")
# How far from its symbol, in symbol sizes and in paper millimetres, a tag may be written.
TAG_REACH_SIZES = 4.0
TAG_REACH_MM = 15.0

SCHEDULE_HEADING = re.compile(r"\bSCHEDULE\b", re.I)
TAG_COLUMN = re.compile(r"^(TAG|TAG\s+NO\.?|REF\.?|REFERENCE|DESIGNATION|MARK|EQUIPMENT\s+NO\.?)$")
# A column's heading, and the attribute its cells state. The first that matches is used.
COLUMNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("duty", re.compile(r"\b(DUTY|STATUS|ROLE)\b")),
    ("flow_l_min", re.compile(r"\b(FLOW|CAPACITY)\b.*\b(L/MIN|LPM|L/S|M3/H|M3/HR)\b")),
    ("head_m", re.compile(r"\bHEAD\b")),
    ("power_kw", re.compile(r"\b(POWER|MOTOR|KW)\b")),
    ("capacity_m3", re.compile(r"\b(CAPACITY|VOLUME)\b")),
    ("driver", re.compile(r"\b(DRIVER|DRIVE|DRIVEN)\b")),
    ("description", re.compile(r"\b(DESCRIPTION|SERVICE|EQUIPMENT|TYPE)\b")),
    ("quantity", re.compile(r"\b(QTY|QUANTITY|NOS?\.?)\b")),
)
NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")
NUMERIC = ("flow_l_min", "head_m", "power_kw", "capacity_m3")
# What a flow column's unit makes of its figure, to give litres a minute.
FLOW_TO_L_MIN = (
    (re.compile(r"\b(L/MIN|LPM)\b"), Decimal(1)),
    (re.compile(r"\bL/S\b"), Decimal(60)),
    (re.compile(r"\bM3/HR?\b"), Decimal(1000) / Decimal(60)),
)

# "L05 FFL +18.000", "LEVEL 5 SSL 18000", "B1 FFL -4.500", "ROOF FFL +22.500".
LEVEL_LINE = re.compile(
    r"^\s*(?:LEVEL\s*|L)?(?P<level>B\d{1,2}|\d{1,2}|ROOF|RF)\b[^+\-\d]*"
    r"\b(?:FFL|SSL|SFL|EL\.?)\s*[:=]?\s*(?P<value>[+-]?\d+(?:\.\d+)?)\s*(?P<unit>MM|M)?\s*$",
    re.I,
)

# What stands on a piece of pipework says which system it is.
SYSTEM_OF_TYPE = {
    "hydrant": "hydrant",
    "hose_reel": "hose_reel",
    "landing_valve": "rising_main",
}
SYSTEM_IN_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("dry_riser", re.compile(r"\bDRY\s+(RISER|RISING\s+MAIN)\b", re.I)),
    ("wet_riser", re.compile(r"\bWET\s+(RISER|RISING\s+MAIN)\b", re.I)),
    ("rising_main", re.compile(r"\bRISING\s+MAIN\b", re.I)),
    ("hose_reel", re.compile(r"\bHOSE\s*REEL\b", re.I)),
    ("hydrant", re.compile(r"\bHYDRANT\b", re.I)),
    ("sprinkler", re.compile(r"\bSPRINKLER\b", re.I)),
)
RISING_MAINS = ("rising_main", "wet_riser", "dry_riser")


def _words(text: Any) -> str:
    return " ".join(str(text or "").upper().split())


def _centre(span: dict[str, Any]) -> tuple[float, float]:
    return (span["minx"] + span["maxx"]) / 2, (span["miny"] + span["maxy"]) / 2


def _height(span: dict[str, Any]) -> float:
    return float(span.get("height") or (span["maxy"] - span["miny"]) or 1.0)


def tag_of(text: Any) -> str | None:
    """The tag a text span is, written one way ("FP 1" and "FP-01" stay as written but
    spaced alike), or None when it is not a tag."""
    match = TAG.match(_words(text))
    if match is None or match.group(1) in NOT_TAG_PREFIXES:
        return None
    return f"{match.group(1)}-{match.group(2)}"


def same_tag(first: str, second: str) -> bool:
    """`FP-1` and `FP-01` are one tag."""
    return _tag_key(first) == _tag_key(second)


def _tag_key(tag: str) -> tuple[str, str]:
    prefix, _, number = tag.partition("-")
    return prefix, number.lstrip("0") or "0"


@dataclass(frozen=True)
class Tagged:
    tag: str
    text: str
    distance_mm: float


def tags(
    symbols: list[tuple[float, float, float]], spans: list[dict[str, Any]]
) -> dict[int, Tagged]:
    """The tag beside each symbol, by its position in `symbols` (x, y, size on paper).

    The nearest pairs are made first, and a tag is given once: two pumps side by side each
    keep their own tag, whichever is nearer the other's.
    """
    found = [(span, tag) for span in spans if (tag := tag_of(span.get("text"))) is not None]
    pairs = []
    for index, (x, y, size) in enumerate(symbols):
        reach = min(TAG_REACH_MM, max(size, 1.0) * TAG_REACH_SIZES)
        for at, (span, _) in enumerate(found):
            distance = math.dist((x, y), _centre(span))
            if distance <= reach:
                pairs.append((distance, index, at))
    out: dict[int, Tagged] = {}
    taken: set[int] = set()
    for distance, index, at in sorted(pairs):
        if index in out or at in taken:
            continue
        span, tag = found[at]
        out[index] = Tagged(tag, str(span["text"]).strip(), round(distance, 3))
        taken.add(at)
    return out


@dataclass(frozen=True)
class ScheduleRow:
    """One row of an equipment schedule: a tag and what the schedule states of it."""

    tag: str
    values: dict[str, str]  # attribute -> value, as takeoff uses it
    quote: str  # the row's cells as written, left to right
    heading: str
    box: tuple[float, float, float, float]
    sheet: str = ""  # the sheet the schedule is drawn on, set by whoever read the sheet
    written: dict[str, str] = field(default_factory=dict)  # attribute -> the cell's words


def _lines(spans: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Text spans as lines of a table: spans whose middles are level with each other, top
    line first whichever way the sheet's y runs is decided by the caller."""
    lines: list[list[dict[str, Any]]] = []
    for span in sorted(spans, key=lambda s: (_centre(s)[1], s["minx"])):
        middle = _centre(span)[1]
        if lines and abs(_centre(lines[-1][0])[1] - middle) <= 0.6 * _height(span):
            lines[-1].append(span)
        else:
            lines.append([span])
    return [sorted(line, key=lambda s: s["minx"]) for line in lines]


def _header(line: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]] | None:
    """A header row's columns as (attribute, heading span), or None if it is not one.

    Only the headings that stand together with the tag column are the table's: a legend or
    a note level with the header, across the sheet, is not a column of it.
    """
    at = next((i for i, span in enumerate(line) if TAG_COLUMN.match(_words(span["text"]))), None)
    if at is None or len(line) < 2:
        return None
    beside = line[at + 1] if at + 1 < len(line) else line[at - 1]
    reach = 3 * abs(beside["minx"] - line[at]["minx"])
    first, last = at, at
    while first > 0 and line[first]["minx"] - line[first - 1]["minx"] <= reach:
        first -= 1
    while last + 1 < len(line) and line[last + 1]["minx"] - line[last]["minx"] <= reach:
        last += 1
    columns: list[tuple[str, dict[str, Any]]] = []
    for span in line[first : last + 1]:
        words = _words(span["text"])
        if TAG_COLUMN.match(words):
            columns.append(("tag", span))
            continue
        name = next((name for name, pattern in COLUMNS if pattern.search(words)), None)
        columns.append((name or f"column:{words}", span))
    return columns if len(columns) >= 2 else None


def _value(name: str, cell: str, heading: str) -> str | None:
    """A cell as takeoff uses it: a number in the attribute's unit, or the words."""
    words = _words(cell)
    if not words or words in ("-", "N/A", "NA"):
        return None
    if name in NUMERIC:
        number = NUMBER.search(words.replace(",", ""))
        if number is None:
            return None
        value = Decimal(number.group())
        if name == "flow_l_min":
            factor = next((f for unit, f in FLOW_TO_L_MIN if unit.search(heading)), None)
            if factor is None:
                return None
            value = (value * factor).quantize(Decimal("0.1"))
        return format(value.normalize(), "f")
    if name == "duty":
        return next((role for role in ("standby", "duty") if role.upper() in words), None)
    if name == "driver":
        return next((kind for kind in ("electric", "diesel") if kind.upper() in words), None)
    return cell.strip()


def schedules(spans: list[dict[str, Any]]) -> list[ScheduleRow]:
    """Every equipment schedule row on a sheet.

    A schedule is a heading with SCHEDULE in it, the nearest header row to it that has a tag
    column, and the lines after the header whose tag cell is a tag. It ends at the first line
    that is not such a row. A cell belongs to the column whose heading it starts nearest.
    """
    spans = [s for s in spans if str(s.get("text") or "").strip() and s.get("minx") is not None]
    lines = _lines(spans)
    rows: list[ScheduleRow] = []
    for heading in (s for s in spans if SCHEDULE_HEADING.search(str(s["text"]))):
        headers = [
            (position, columns)
            for position, line in enumerate(lines)
            if (columns := _header(line)) is not None and heading not in line
        ]
        if not headers:
            continue
        middle = _centre(heading)[1]
        position, columns = min(headers, key=lambda h: abs(_centre(lines[h[0]][0])[1] - middle))
        header_y = _centre(lines[position][0])[1]
        if abs(header_y - middle) > 6 * _height(heading):
            continue  # a header that far off belongs to some other table
        # Rows run away from the heading: down the page, whichever way y is counted.
        step = 1 if header_y > middle else -1
        starts = [span["minx"] for _, span in columns]
        # The table's width: from its first heading to a column's width past its last.
        widest = max(b - a for a, b in pairwise(starts))
        left = starts[0] - _height(heading)
        right = max(span["maxx"] for _, span in columns) + widest
        pitch = None
        at = position + step
        previous_y = header_y
        while 0 <= at < len(lines):
            line = [span for span in lines[at] if left <= span["minx"] <= right]
            at += step
            tag_at = starts[[name for name, _ in columns].index("tag")]
            if not any(abs(span["minx"] - tag_at) <= _height(span) for span in line):
                continue  # something else on the sheet between the rows: not of the table
            line_y = _centre(line[0])[1]
            gap = abs(line_y - previous_y)
            if pitch is not None and gap > 2.5 * pitch:
                break
            cells: dict[str, list[str]] = {}
            for span in line:
                nearest = min(range(len(starts)), key=lambda i: abs(starts[i] - span["minx"]))
                cells.setdefault(columns[nearest][0], []).append(str(span["text"]).strip())
            tag = tag_of(" ".join(cells.get("tag", [])))
            if tag is None:
                break
            pitch = pitch or gap
            previous_y = line_y
            values: dict[str, str] = {}
            written: dict[str, str] = {}
            for (name, heading_span), _ in zip(columns, starts, strict=True):
                if name == "tag" or name.startswith("column:") or name not in cells:
                    continue
                cell = " ".join(cells[name])
                value = _value(name, cell, _words(heading_span["text"]))
                if value is not None:
                    values[name], written[name] = value, cell
            rows.append(
                ScheduleRow(
                    tag=tag,
                    values=values,
                    quote=" | ".join(str(span["text"]).strip() for span in line),
                    heading=str(heading["text"]).strip(),
                    box=(
                        min(s["minx"] for s in line),
                        min(s["miny"] for s in line),
                        max(s["maxx"] for s in line),
                        max(s["maxy"] for s in line),
                    ),
                    written=written,
                )
            )
    unique: dict[tuple[str, str], ScheduleRow] = {}
    for row in rows:
        unique.setdefault((row.tag, row.quote), row)
    return list(unique.values())


@dataclass(frozen=True)
class LevelMark:
    level: str  # L05, B1, RF: the drawing-number forms
    elevation_mm: int
    text: str
    y: float = 0.0  # where on the sheet the level is named


def level_marks(spans: list[dict[str, Any]]) -> list[LevelMark]:
    """The levels a sheet names with their finished floor levels, lowest first.

    A figure with a decimal point is metres ("+18.000"); a whole number of a thousand or
    more is millimetres; a unit written after it decides either way.
    """
    found: dict[str, LevelMark] = {}
    for span in spans:
        match = LEVEL_LINE.match(str(span.get("text") or ""))
        if match is None:
            continue
        name = match.group("level").upper()
        if name.isdigit():
            name = f"L{int(name):02d}"
        elif name == "ROOF":
            name = "RF"
        figure, unit = match.group("value"), (match.group("unit") or "").upper()
        value = Decimal(figure)
        metres = unit == "M" or (not unit and ("." in figure or abs(value) < 1000))
        elevation = int((value * 1000).to_integral_value()) if metres else int(value)
        found.setdefault(
            name, LevelMark(name, elevation, str(span["text"]).strip(), _centre(span)[1])
        )
    return sorted(found.values(), key=lambda mark: mark.elevation_mm)


def level_at(marks: list[LevelMark], y: float) -> LevelMark | None:
    """The level a schematic draws something on: the level named nearest its height on the
    sheet, no further off than the levels are drawn from each other at their widest."""
    if not marks:
        return None
    heights = sorted(mark.y for mark in marks)
    pitch = max((b - a for a, b in pairwise(heights)), default=None)
    nearest = min(marks, key=lambda mark: abs(mark.y - y))
    if pitch is not None and abs(nearest.y - y) > pitch:
        return None
    return nearest


def system_of(object_types: set[str], categories: set[str], descriptions: list[str]) -> str | None:
    """The system a connected piece of pipework belongs to, or None when what stands on it
    does not say, or says more than one.

    A wet or dry rising main named in a riser's description is the same system as the
    landing valves on it, said more exactly.
    """
    systems = {SYSTEM_OF_TYPE[kind] for kind in object_types if kind in SYSTEM_OF_TYPE}
    if "sprinkler" in categories:
        systems.add("sprinkler")
    for words in descriptions:
        named = next((name for name, pattern in SYSTEM_IN_WORDS if pattern.search(words)), None)
        if named:
            systems.add(named)
    exact = systems & {"wet_riser", "dry_riser"}
    if len(exact) == 1:
        systems.discard("rising_main")
    if not systems and "breeching_inlet" in object_types:
        return "rising_main"
    return next(iter(systems)) if len(systems) == 1 else None
