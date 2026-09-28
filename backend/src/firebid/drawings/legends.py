"""Legends: where a consultant says what each symbol means (FR-VIS-02).

A legend is a heading (LEGEND, SYMBOLS, SYMBOL LEGEND, LEGEND AND SYMBOLS) with rows beneath
it: a symbol, then its description to the right on the same line. Many consultants give no
such heading and set the legend out in category columns instead ("FIRE FIGHTING & ALARM
SYSTEM", "VALVES & ACCESSORIES"): a short upper-case line with a run of symbol rows directly
beneath it heads a legend column too. A column ends where the next heading on its line
starts. It may fill a dedicated
legend sheet or sit in a corner of a plan. The title block is never a legend, even when the
sheet's title is "LEGEND AND SYMBOLS".

Rows run down from the heading at a steady pitch and stop at the first gap much larger than
it. A row whose description has no symbol beside it (an ABBREVIATIONS table, a note) is not
a symbol row and is left out.

The legend's own region is returned too: its example symbols are not installed anywhere,
so instances inside it are never counted.

Pure: geometry in, legends out.
"""

from __future__ import annotations

import bisect
import itertools
import re
import statistics
from dataclasses import dataclass, replace
from typing import Any

import pyarrow as pa

from firebid.drawings.geometry import texts
from firebid.drawings.symbols import Cluster, clusters, signature_of
from firebid.drawings.title_block import Box, Span, locate

HEADING = re.compile(
    r"^(?:SYMBOLS?\s+LEGEND|LEGEND(?:\s+(?:AND|&)\s+SYMBOLS?)?|SYMBOLS?|LEGENDS)\s*:?$", re.I
)
# A legend column is at most this wide and this tall on paper.
COLUMN_MM = 180.0
DEPTH_MM = 400.0
# A symbol sits at most this far to the left of its description.
SYMBOL_REACH_MM = 40.0
# A gap this many times the row pitch ends the legend.
GAP_FACTOR = 2.5
# A category heading: a few upper-case words, no digits, heading at least this many symbol
# rows set at a steady pitch, the first of them close beneath it.
CATEGORY = re.compile(r"^[A-Z][A-Z&/,()'.\- ]{2,60}$")
CATEGORY_WORDS = 6
# A legend row's description reads as words ("GATE VALVE"), not a code ("L17-TW.B-30").
WORDY = re.compile(r"[A-Z]{4,}", re.I)
CATEGORY_ROWS = 4
FIRST_ROW_HEIGHTS = 6.0
STEADY = 0.35
# A row whose sample is a line (a pipework line type) or a sliver of one is no point symbol:
# matched as one, every stroke of pipe on a plan would be an object. Narrower than this, or
# this many times longer than wide.
LINE_SAMPLE_MM = 0.3
LINE_SAMPLE_ASPECT = 5.0


@dataclass(frozen=True)
class LegendRow:
    description: str
    symbol: Cluster
    row_box: tuple[float, float, float, float]


@dataclass(frozen=True)
class Legend:
    heading: str
    box: tuple[float, float, float, float]
    rows: tuple[LegendRow, ...]


def detect(table: pa.Table, page: tuple[float, float, float, float]) -> list[Legend]:
    spans = [span for span in texts(table) if span["text"] and span["minx"] is not None]
    region = _title_block(spans, page)
    outside = [span for span in spans if not _in(span, region)]
    named = [span for span in outside if HEADING.match(" ".join(span["text"].split()))]
    lines = _lines(outside)
    # Where the shapes are is enough to find the rows; only the symbols kept as legend rows
    # are signed, which on a busy plan is thousands of signatures fewer.
    shapes = _Shapes(clusters(table, signed=False))
    categories = [line for line in _category_headings(lines, shapes) if not _covers(named, line)]
    headings = named + categories
    legends = []
    for heading in headings:
        legend = _legend(heading, lines, shapes, headings, category=heading in categories)
        if legend is not None:
            legends.append(legend)
    return _signed(table, _distinct(legends))


def line_sample(symbol: Cluster) -> bool:
    """A legend row's sample that is a line, not a symbol: pipework drawn as a short run."""
    width = symbol.box[2] - symbol.box[0]
    height = symbol.box[3] - symbol.box[1]
    narrow, long = min(width, height), max(width, height)
    return narrow < LINE_SAMPLE_MM or long >= LINE_SAMPLE_ASPECT * narrow


def _signed(table: pa.Table, legends: list[Legend]) -> list[Legend]:
    out = []
    for legend in legends:
        rows = []
        for row in legend.rows:
            if line_sample(row.symbol):
                continue
            signature = signature_of(table, row.symbol)
            if signature is not None:
                rows.append(
                    LegendRow(
                        row.description, replace(row.symbol, signature=signature), row.row_box
                    )
                )
        if rows:
            out.append(Legend(legend.heading, legend.box, tuple(rows)))
    return out


def _distinct(legends: list[Legend]) -> list[Legend]:
    """A row belongs to one legend: the one whose heading sits nearest above it."""
    owner: dict[int, tuple[float, int]] = {}
    for index, legend in enumerate(legends):
        for row in legend.rows:
            distance = row.row_box[1] - legend.box[1]
            key = id(row.symbol)
            if key not in owner or distance < owner[key][0]:
                owner[key] = (distance, index)
    out = []
    for index, legend in enumerate(legends):
        rows = tuple(row for row in legend.rows if owner[id(row.symbol)][1] == index)
        if rows:
            out.append(Legend(legend.heading, legend.box, rows))
    return out


def _wordy(text: str) -> bool:
    """A word of four letters or more, and mostly letters: STRAINER, GATE VALVE, not DN100,
    L17-TW.B-30 or RM 4."""
    characters = [c for c in text if c.isalnum()]
    letters = sum(c.isalpha() for c in characters)
    return bool(WORDY.search(text)) and letters >= 0.6 * len(characters)


class _Shapes:
    """The sheet's candidate symbols, indexed by height on the page for quick lookup."""

    def __init__(self, found: list[Cluster]) -> None:
        self.found = sorted(found, key=lambda cluster: cluster.centre[1])
        self.ys = [cluster.centre[1] for cluster in self.found]

    def near(self, y: float, reach: float) -> list[Cluster]:
        low = bisect.bisect_left(self.ys, y - reach)
        high = bisect.bisect_right(self.ys, y + reach)
        return self.found[low:high]


def _covers(named: list[dict[str, Any]], line: dict[str, Any]) -> bool:
    return any(
        abs(span["miny"] - line["miny"]) < 1.0 and abs(span["minx"] - line["minx"]) < 1.0
        for span in named
    )


def _category_headings(lines: list[dict[str, Any]], shapes: _Shapes) -> list[dict[str, Any]]:
    """Short upper-case lines with a steady run of symbol rows directly beneath them.

    The rows must read as descriptions, not codes: on a plan, room names and pipe sizes
    stack up beside symbols too.
    """
    ordered = sorted(lines, key=lambda line: line["miny"])
    tops = [line["miny"] for line in ordered]
    symbol_of: dict[int, bool] = {}

    def has_symbol(line: dict[str, Any]) -> bool:
        key = id(line)
        if key not in symbol_of:
            symbol_of[key] = _symbol_for(line, shapes, set()) is not None
        return symbol_of[key]

    def shaped_like_one(line: dict[str, Any]) -> bool:
        text = " ".join(line["text"].split())
        return bool(CATEGORY.match(text)) and 2 <= len(text.split()) <= CATEGORY_WORDS

    shaped = [line for line in ordered if shaped_like_one(line) and not has_symbol(line)]
    out = []
    for line in shaped:
        height = max(line["maxy"] - line["miny"], 1.0)
        middle = (line["miny"] + line["maxy"]) / 2
        left = line["minx"] - 10.0
        # Columns side by side: this one ends where the next heading on its line starts.
        right = min(
            [
                line["minx"] + COLUMN_MM,
                *(
                    other["minx"] - 1.0
                    for other in shaped
                    if other is not line
                    and other["minx"] > line["maxx"]
                    and abs((other["miny"] + other["maxy"]) / 2 - middle) < height
                ),
            ]
        )
        centres: list[float] = []
        reach = FIRST_ROW_HEIGHTS * height
        for other in ordered[bisect.bisect_right(tops, line["maxy"]) :]:
            if not left <= other["minx"] <= right:
                continue
            centre = (other["miny"] + other["maxy"]) / 2
            last = centres[-1] if centres else line["maxy"]
            if centre - last > reach:
                break
            if not _wordy(other["text"]) or not has_symbol(other):
                continue
            if centres:
                reach = GAP_FACTOR * (centre - last) if len(centres) == 1 else reach
            centres.append(centre)
            if len(centres) >= CATEGORY_ROWS:
                break
        if len(centres) < CATEGORY_ROWS:
            continue
        pitches = [b - a for a, b in itertools.pairwise(centres)]
        middle = statistics.median(pitches)
        if middle > 0 and all(abs(pitch - middle) <= STEADY * middle for pitch in pitches):
            out.append(line)
    return out


def _title_block(
    spans: list[dict[str, Any]], page: tuple[float, float, float, float]
) -> Box | None:
    readable = [
        Span(str(span["text"]), span["minx"], span["miny"], span["maxx"], span["maxy"])
        for span in spans
    ]
    return locate(readable, Box(*page))


def _in(span: dict[str, Any], region: Box | None) -> bool:
    if region is None:
        return False
    cx, cy = (span["minx"] + span["maxx"]) / 2, (span["miny"] + span["maxy"]) / 2
    return bool(region.x0 <= cx <= region.x1 and region.y0 <= cy <= region.y1)


def _legend(
    heading: dict[str, Any],
    lines: list[dict[str, Any]],
    candidates: _Shapes,
    headings: list[dict[str, Any]],
    *,
    category: bool = False,
) -> Legend | None:
    """The rows beneath one heading. Under a category heading, only rows that read as words
    count, and it takes a run of them: a room label over a stack of codes is no legend."""
    left = heading["minx"] - 10.0
    height = max(heading["maxy"] - heading["miny"], 1.0)
    middle = (heading["miny"] + heading["maxy"]) / 2
    # The column ends where the next text on the heading's own line starts: the next
    # heading, or whatever else is set beside it.
    beside = [
        other["minx"]
        for other in [*headings, *lines]
        if other is not heading
        and other["minx"] > heading["maxx"]
        and abs((other["miny"] + other["maxy"]) / 2 - middle) < height
    ]
    right = min([heading["minx"] + COLUMN_MM, *(x - 1.0 for x in beside)])
    top = heading["maxy"]
    # And below, at the next heading that overlaps the column.
    others = [
        other["miny"]
        for other in headings
        if other is not heading
        and other["miny"] > top
        and other["minx"] <= right
        and other["maxx"] >= left
    ]
    bottom = min([top + DEPTH_MM, *others])
    below = sorted(
        (line for line in lines if left <= line["minx"] <= right and top < line["miny"] < bottom),
        key=lambda line: line["miny"],
    )
    rows: list[LegendRow] = []
    taken: set[int] = set()
    previous: float | None = None
    pitches: list[float] = []
    for line in below:
        centre = (line["miny"] + line["maxy"]) / 2
        if (
            previous is not None
            and pitches
            and (centre - previous > GAP_FACTOR * statistics.median(pitches))
        ):
            break
        if category and not _wordy(line["text"]):
            continue
        symbol = _symbol_for(line, candidates, taken)
        if symbol is None:
            continue
        if previous is not None:
            pitches.append(centre - previous)
        previous = centre
        taken.add(id(symbol))
        box = (
            min(symbol.box[0], line["minx"]),
            min(symbol.box[1], line["miny"]),
            max(symbol.box[2], line["maxx"]),
            max(symbol.box[3], line["maxy"]),
        )
        rows.append(LegendRow(" ".join(line["text"].split()), symbol, box))
    if not rows or (category and len(rows) < CATEGORY_ROWS):
        return None
    box = (
        min([heading["minx"], *(row.row_box[0] for row in rows)]),
        heading["miny"],
        max([heading["maxx"], *(row.row_box[2] for row in rows)]),
        max(row.row_box[3] for row in rows),
    )
    return Legend(" ".join(heading["text"].split()).upper(), box, tuple(rows))


def _symbol_for(line: dict[str, Any], candidates: _Shapes, taken: set[int]) -> Cluster | None:
    """The symbol just left of a description, on its line."""
    height = max(line["maxy"] - line["miny"], 1.0)
    centre = (line["miny"] + line["maxy"]) / 2
    best: tuple[float, Cluster] | None = None
    for cluster in candidates.near(centre, max(height, 10.0) * 2):
        if id(cluster) in taken:
            continue
        _, cy = cluster.centre
        size = cluster.box[3] - cluster.box[1]
        if abs(cy - centre) > max(height, size) * 0.8:
            continue
        gap = line["minx"] - cluster.box[2]
        if -0.5 <= gap <= SYMBOL_REACH_MM and (best is None or gap < best[0]):
            best = (gap, cluster)
    return best[1] if best else None


def _lines(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Spans on one line and close together, joined: "GATE" + "VALVE" is one description."""
    ordered = sorted(
        spans, key=lambda span: (round((span["miny"] + span["maxy"]) / 2, 0), span["minx"])
    )
    lines: list[dict[str, Any]] = []
    for span in ordered:
        last = lines[-1] if lines else None
        height = max(span["maxy"] - span["miny"], 0.5)
        if (
            last is not None
            and abs((last["miny"] + last["maxy"]) / 2 - (span["miny"] + span["maxy"]) / 2)
            < 0.5 * height
            and 0 <= span["minx"] - last["maxx"] < 1.5 * height
        ):
            last["text"] = f"{last['text']} {span['text']}"
            last["maxx"] = max(last["maxx"], span["maxx"])
            last["miny"] = min(last["miny"], span["miny"])
            last["maxy"] = max(last["maxy"], span["maxy"])
            continue
        lines.append(dict(span))
    return lines
