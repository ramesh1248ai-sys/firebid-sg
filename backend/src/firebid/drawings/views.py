"""The views on a sheet: what each one is, where it is, and at what scale (FR-VIS-08).

A sheet is often more than one drawing: a plan with a key plan in the corner, sections beside
it, an enlarged plan of the riser room. Each view has its own title and its own scale, and
the kinds matter downstream: an enlarged plan repeats what the general arrangement shows, so
its sprinklers must not be counted twice (P1-07's de-duplication starts here).

Views come from the source where it defines them exactly: a DXF viewport (P1-03 C). Otherwise
they are found by their titles, which follow a pattern on every sheet: a line of capitals
naming the view (PLAN, ENLARGED PLAN, SECTION A-A, SCHEMATIC, KEY PLAN, DETAIL), with the scale
beneath. Line work belongs to the view whose title it is nearest. The title block is not a
view and is left out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np
import pyarrow as pa

from firebid.drawings import grids
from firebid.drawings.geometry import segments, texts
from firebid.drawings.grids import GridSystem
from firebid.drawings.scale import Stated, Verdict, evidence_in, parse_stated, verify
from firebid.drawings.title_block import Box, Span, locate

# Bumped when detection changes, so every sheet's views are detected again.
DETECTOR_VERSION = "1"


class ViewKind(StrEnum):
    PLAN = "plan"
    ENLARGED_PLAN = "enlarged plan"
    KEY_PLAN = "key plan"
    SECTION = "section"
    ELEVATION = "elevation"
    SCHEMATIC = "schematic"
    DETAIL = "detail"


# Most specific first: "ENLARGED PLAN" is an enlarged plan, not a plan.
TITLES: tuple[tuple[ViewKind, re.Pattern[str]], ...] = (
    (ViewKind.ENLARGED_PLAN, re.compile(r"\bENLARGED\b.*\bPLAN\b|\bPLAN\b.*\bENLARGED\b", re.I)),
    (ViewKind.KEY_PLAN, re.compile(r"\bKEY\s+PLAN\b", re.I)),
    (
        ViewKind.SECTION,
        re.compile(r"\bSECTION\b(\s+[A-Z0-9]{1,3}\s*[-\N{EN DASH}]\s*[A-Z0-9]{1,3})?", re.I),
    ),
    (ViewKind.ELEVATION, re.compile(r"\bELEVATION\b", re.I)),
    (ViewKind.SCHEMATIC, re.compile(r"\bSCHEMATIC\b|\bRISER\s+(DIAGRAM|SCHEMATIC)\b", re.I)),
    (ViewKind.DETAIL, re.compile(r"\bDETAILS?\b", re.I)),
    (ViewKind.PLAN, re.compile(r"\bPLAN\b|\bLAYOUT\b", re.I)),
)
LEVEL_IN_TITLE = re.compile(r"\b(?:LEVEL|LVL|STOREY)\s*(\d{1,2}|B\d|ROOF)\b", re.I)


@dataclass(frozen=True)
class View:
    kind: ViewKind
    title: str | None
    extent: tuple[float, float, float, float]  # sheet mm, y down
    stated: Stated
    source: str  # viewport | title | sheet
    level: str | None = None
    viewport_denominator: float | None = None  # exact, from a DXF viewport
    notes: tuple[str, ...] = field(default_factory=tuple)


def kind_of(title: str) -> ViewKind | None:
    for kind, pattern in TITLES:
        if pattern.search(title):
            return kind
    return None


def level_of(title: str | None) -> str | None:
    """`LEVEL 5 ...` is L05; `LEVEL B1` is B1; `ROOF` is RF: the drawing-number forms."""
    match = LEVEL_IN_TITLE.search(title or "")
    if not match:
        return None
    value = match.group(1).upper()
    if value.isdigit():
        return f"L{int(value):02d}"
    return "RF" if value == "ROOF" else value


def detect(
    table: pa.Table,
    page: tuple[float, float, float, float],
    sheet_scale: str | None,
    source_views: list[dict[str, Any]] | None = None,
) -> list[View]:
    """The sheet's views: exact ones from the source, else found by their titles."""
    spans = texts(table)
    region = _title_block_region(spans, page)
    titles = [
        span
        for span in spans
        if span["text"]
        and kind_of(span["text"])
        and not _inside(span, region)
        and len(span["text"]) >= 4
    ]
    viewports = [view for view in source_views or [] if view.get("kind") == "viewport"]
    if viewports:
        return [_from_viewport(view, titles, spans, sheet_scale) for view in viewports]

    extent = _drawing_area(table, page, region)
    if not titles:
        stated = parse_stated(sheet_scale)
        kind = ViewKind.SCHEMATIC if stated.nts else ViewKind.PLAN
        return [
            View(
                kind,
                None,
                extent,
                stated,
                "sheet",
                notes=("no view title; the whole drawing area is one view",),
            )
        ]
    if len(titles) == 1:
        return [_titled(titles[0], extent, spans, sheet_scale)]
    return _split(table, titles, spans, sheet_scale, region)


def _title_block_region(
    spans: list[dict[str, Any]], page: tuple[float, float, float, float]
) -> Box | None:
    readable = [
        Span(
            str(span["text"]),
            float(span["minx"]),
            float(span["miny"]),
            float(span["maxx"]),
            float(span["maxy"]),
        )
        for span in spans
        if span["text"]
    ]
    return locate(readable, Box(*page))


def _inside(span: dict[str, Any], region: Box | None) -> bool:
    if region is None:
        return False
    cx = (float(span["minx"]) + float(span["maxx"])) / 2
    cy = (float(span["miny"]) + float(span["maxy"])) / 2
    return region.x0 <= cx <= region.x1 and region.y0 <= cy <= region.y1


def _drawing_area(
    table: pa.Table, page: tuple[float, float, float, float], region: Box | None
) -> tuple[float, float, float, float]:
    """The box around the drawing that is neither the border nor the title block.

    Built from each primitive's box, not its segments: circles count (a schematic may be all
    symbols), and a border is dropped whole, by spanning most of the page.
    """
    boxes = table.select(["kind", "minx", "miny", "maxx", "maxy"]).to_pylist()
    width, height = page[2] - page[0], page[3] - page[1]
    kept = []
    for item in boxes:
        if item["kind"] == "text" or item["minx"] is None:
            continue
        if (
            item["maxx"] - item["minx"] >= 0.9 * width
            or item["maxy"] - item["miny"] >= 0.9 * height
        ):
            continue
        cx, cy = (item["minx"] + item["maxx"]) / 2, (item["miny"] + item["maxy"]) / 2
        if region is not None and region.x0 <= cx <= region.x1 and region.y0 <= cy <= region.y1:
            continue
        kept.append((item["minx"], item["miny"], item["maxx"], item["maxy"]))
    if not kept:
        return page
    found = np.asarray(kept, dtype=float)
    return (
        float(found[:, 0].min()),
        float(found[:, 1].min()),
        float(found[:, 2].max()),
        float(found[:, 3].max()),
    )


def _scale_near(
    title: dict[str, Any], spans: list[dict[str, Any]], sheet_scale: str | None
) -> Stated:
    """A view's own scale, written just beneath its title, else the title's own words, else
    the sheet's."""
    height = max(float(title["maxy"]) - float(title["miny"]), 1.0)
    for span in spans:
        if span is title or not span["text"]:
            continue
        below = 0 <= float(span["miny"]) - float(title["maxy"]) <= 4 * height
        aligned = abs(float(span["minx"]) - float(title["minx"])) <= 3 * height
        if below and aligned:
            stated = parse_stated(str(span["text"]))
            if stated.denominator or stated.nts:
                return stated
    own = parse_stated(str(title["text"]))
    return own if own.denominator or own.nts else parse_stated(sheet_scale)


def _titled(
    title: dict[str, Any],
    extent: tuple[float, float, float, float],
    spans: list[dict[str, Any]],
    sheet_scale: str | None,
) -> View:
    text = str(title["text"])
    return View(
        kind=kind_of(text) or ViewKind.PLAN,
        title=text,
        extent=extent,
        stated=_scale_near(title, spans, sheet_scale),
        source="title",
        level=level_of(text),
    )


def _split(
    table: pa.Table,
    titles: list[dict[str, Any]],
    spans: list[dict[str, Any]],
    sheet_scale: str | None,
    region: Box | None,
) -> list[View]:
    """Several titled views: each line gets the view whose title it is nearest."""
    lines = segments(table)
    centres = np.asarray(
        [
            ((float(t["minx"]) + float(t["maxx"])) / 2, (float(t["miny"]) + float(t["maxy"])) / 2)
            for t in titles
        ]
    )
    mid_x, mid_y = (lines.x0 + lines.x1) / 2, (lines.y0 + lines.y1) / 2
    outside = lines.lengths > 0
    if region is not None:
        outside &= ~(
            (mid_x >= region.x0)
            & (mid_x <= region.x1)
            & (mid_y >= region.y0)
            & (mid_y <= region.y1)
        )
    owner = np.argmin(
        np.hypot(mid_x[:, None] - centres[None, :, 0], mid_y[:, None] - centres[None, :, 1]), axis=1
    )
    views = []
    for index, title in enumerate(titles):
        mine = outside & (owner == index)
        if mine.any():
            xs = np.concatenate([lines.x0[mine], lines.x1[mine]])
            ys = np.concatenate([lines.y0[mine], lines.y1[mine]])
            extent = (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))
        else:
            extent = (
                float(title["minx"]),
                float(title["miny"]),
                float(title["maxx"]),
                float(title["maxy"]),
            )
        views.append(_titled(title, extent, spans, sheet_scale))
    return views


def _from_viewport(
    view: dict[str, Any],
    titles: list[dict[str, Any]],
    spans: list[dict[str, Any]],
    sheet_scale: str | None,
) -> View:
    extent = tuple(float(value) for value in view["extent"])
    denominator = float(view["denominator"])
    inside = [
        title
        for title in titles
        if extent[0] - 20 <= float(title["minx"]) <= extent[2] + 20
        and extent[1] - 20 <= float(title["miny"]) <= extent[3] + 40
    ]
    title = str(inside[0]["text"]) if inside else None
    stated = (
        _scale_near(inside[0], spans, sheet_scale)
        if inside
        else Stated(round(denominator, 3), False, f"1:{denominator:g} (viewport)")
    )
    return View(
        kind=kind_of(title or "") or ViewKind.PLAN,
        title=title,
        extent=(extent[0], extent[1], extent[2], extent[3]),
        stated=stated,
        source="viewport",
        level=level_of(title),
        viewport_denominator=denominator,
    )


@dataclass(frozen=True)
class Analysed:
    """A view with what the drawing proves about it: its scale verdict and its grid."""

    view: View
    verdict: Verdict
    grid: GridSystem | None


def analyse(
    table: pa.Table,
    page: tuple[float, float, float, float],
    sheet_scale: str | None,
    source_views: list[dict[str, Any]] | None = None,
) -> list[Analysed]:
    """Every view on the sheet, its scale checked against its own dimensions.

    The structural grid is found once per sheet: views of one sheet share its gridlines, and
    a view without them (a schematic, a detail) is given none.
    """
    grid = grids.detect(table)
    found = []
    for view in detect(table, page, sheet_scale, source_views):
        verdict = verify(view.stated, evidence_in(table, view.extent))
        on_grid = grid if view.kind in GRIDDED and _overlaps(grid, view.extent) else None
        found.append(Analysed(view, verdict, on_grid))
    return found


GRIDDED = frozenset({ViewKind.PLAN, ViewKind.ENLARGED_PLAN, ViewKind.KEY_PLAN})


def _overlaps(grid: GridSystem | None, extent: tuple[float, float, float, float]) -> bool:
    if grid is None:
        return False
    xs = [line.position for line in grid.across]
    ys = [line.position for line in grid.up]
    return (
        min(xs) <= extent[2]
        and max(xs) >= extent[0]
        and min(ys) <= extent[3]
        and max(ys) >= extent[1]
    )
