"""Legends: where a consultant says what each symbol means (FR-VIS-02).

A legend is a heading (LEGEND, SYMBOLS, SYMBOL LEGEND, LEGEND AND SYMBOLS) with rows beneath
it: a symbol, then its description to the right on the same line. It may fill a dedicated
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

import re
import statistics
from dataclasses import dataclass
from typing import Any

import pyarrow as pa

from firebid.drawings.geometry import texts
from firebid.drawings.symbols import Cluster, clusters
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
    headings = [span for span in outside if HEADING.match(" ".join(span["text"].split()))]
    if not headings:
        return []
    candidates = clusters(table)
    legends = []
    for heading in headings:
        legend = _legend(heading, outside, candidates, headings)
        if legend is not None:
            legends.append(legend)
    return legends


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
    spans: list[dict[str, Any]],
    candidates: list[Cluster],
    headings: list[dict[str, Any]],
) -> Legend | None:
    left = heading["minx"] - 10.0
    right = heading["minx"] + COLUMN_MM
    top = heading["maxy"]
    others = [other["miny"] for other in headings if other is not heading and other["miny"] > top]
    bottom = min([top + DEPTH_MM, *others])
    below = sorted(
        (
            line
            for line in _lines(spans)
            if left <= line["minx"] <= right and top < line["miny"] < bottom
        ),
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
    if not rows:
        return None
    box = (
        min([heading["minx"], *(row.row_box[0] for row in rows)]),
        heading["miny"],
        max([heading["maxx"], *(row.row_box[2] for row in rows)]),
        max(row.row_box[3] for row in rows),
    )
    return Legend(" ".join(heading["text"].split()).upper(), box, tuple(rows))


def _symbol_for(line: dict[str, Any], candidates: list[Cluster], taken: set[int]) -> Cluster | None:
    """The symbol just left of a description, on its line."""
    height = max(line["maxy"] - line["miny"], 1.0)
    centre = (line["miny"] + line["maxy"]) / 2
    best: tuple[float, Cluster] | None = None
    for cluster in candidates:
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
