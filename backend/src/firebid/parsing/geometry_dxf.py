"""Geometry from a DXF layout, in sheet millimetres. Runs in the sandbox.

* **Paperspace** entities are on paper already, in paper millimetres.
* **Modelspace seen through a viewport** is clipped to the viewport and carried to paper by
  the viewport's own scale, which is exact: it becomes the view's scale.
* **A file with only modelspace** is placed on paper at the scale it states (`1:100`), or,
  failing that, at the ratio of its extents to its page setup, or to fit an A1 sheet. That is
  what its PDF export would have done, so the two formats share sheet coordinates.

Block inserts are exploded into primitives for measurement, and kept as `insert` rows as
well, because symbols are matched on the insert (P1-04). Dimension entities are kept with
their defining points and measured value, because they are how a scale is verified.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from firebid.drawings.geometry import Builder, Method, to_parquet
from firebid.parsing.dxf import FALLBACK_SIZE_MM, _read, stated_scale

# Nested blocks deeper than this are pathological; stop exploding.
MAX_INSERT_DEPTH = 8
# Curves are flattened to within this many drawing units of the true curve.
FLATTEN_DISTANCE = 0.5


@dataclass(frozen=True)
class Placement:
    """How drawing coordinates map to the sheet: scale, then flip y about the top."""

    scale: float  # sheet mm per drawing unit
    origin_x: float  # drawing x at the sheet's left edge
    origin_y: float  # drawing y at the sheet's top edge

    def to_sheet(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.origin_x) * self.scale, (self.origin_y - y) * self.scale


def extract(payload: bytes, layout_name: str | None) -> dict[str, Any]:
    """Primitives, dimensions and views of one layout, as Parquet plus plain view data."""
    from ezdxf import bbox

    document = _read(payload)
    names = [name for name in document.layouts.names() if name.lower() != "model"]
    paper = (
        document.layouts.get(layout_name)
        if layout_name and layout_name in names and len(document.layouts.get(layout_name))
        else None
    )
    builder = Builder(Method.CAD)
    views: list[dict[str, Any]] = []

    if paper is not None:
        width = float(getattr(paper.dxf, "paper_width", 0) or 0) or FALLBACK_SIZE_MM[0]
        height = float(getattr(paper.dxf, "paper_height", 0) or 0) or FALLBACK_SIZE_MM[1]
        extents = bbox.extents(paper, fast=True)
        left = float(extents.extmin.x) if extents.has_data else 0.0
        placement = Placement(1.0, left, (left and float(extents.extmax.y)) or height)
        placement = Placement(1.0, 0.0, height) if not extents.has_data else placement
        _entities(paper, placement, builder, skip_viewports=True)
        for viewport in paper.query("VIEWPORT"):
            view = _through_viewport(document, viewport, placement, builder)
            if view is not None:
                views.append(view)
        page = [0.0, 0.0, width, height]
    else:
        model = document.modelspace()
        extents = bbox.extents(model, fast=True)
        if not extents.has_data:
            return {
                "parquet": to_parquet(builder.table()),
                "method": str(Method.CAD),
                "views": [],
                "page": [0.0, 0.0, *FALLBACK_SIZE_MM],
            }
        denominator, source = _model_denominator(model, extents)
        placement = Placement(1.0 / denominator, float(extents.extmin.x), float(extents.extmax.y))
        _entities(model, placement, builder)
        page = [0.0, 0.0, float(extents.size.x) / denominator, float(extents.size.y) / denominator]
        views.append(
            {"kind": "model", "extent": page, "denominator": denominator, "scale_source": source}
        )

    return {
        "parquet": to_parquet(builder.table()),
        "method": str(Method.CAD),
        "views": views,
        "page": page,
    }


def _model_denominator(model: Any, extents: Any) -> tuple[float, str]:
    stated = stated_scale(model)
    if stated:
        return float(stated), "stated"
    paper_width = float(getattr(model.dxf, "paper_width", 0) or 0)
    if paper_width > 10:
        return max(float(extents.size.x) / paper_width, 1e-9), "page_setup"
    return max(float(extents.size.x) / FALLBACK_SIZE_MM[0], 1e-9), "fitted"


def _through_viewport(
    document: Any, viewport: Any, paper: Placement, builder: Builder
) -> dict[str, Any] | None:
    """Modelspace, clipped to one viewport and scaled onto the paper."""
    if viewport.dxf.get("id", 2) == 1:
        return None  # the overall paperspace viewport, not a view of the model
    width, height = float(viewport.dxf.width), float(viewport.dxf.height)
    view_height = float(viewport.dxf.view_height or 0)
    if width <= 0 or height <= 0 or view_height <= 0:
        return None
    scale = height / view_height  # paper units per model unit
    cx, cy = float(viewport.dxf.center.x), float(viewport.dxf.center.y)
    vx, vy = float(viewport.dxf.view_center_point.x), float(viewport.dxf.view_center_point.y)
    # Model (x, y) lands on paper at centre + (point - view centre) * scale.
    origin_x = vx - cx / scale
    origin_y = vy + (paper.origin_y - cy) / scale
    placement = Placement(scale * paper.scale, origin_x, origin_y)
    left, top = paper.to_sheet(cx - width / 2, cy + height / 2)
    right, bottom = paper.to_sheet(cx + width / 2, cy - height / 2)
    clip = (left, top, right, bottom)
    before = len(builder.columns["kind"])
    _entities(document.modelspace(), placement, builder)
    _clip_from(builder, before, clip)
    return {
        "kind": "viewport",
        "extent": list(clip),
        "denominator": 1.0 / scale,
        "scale_source": "viewport",
    }


def _clip_from(builder: Builder, start: int, clip: tuple[float, float, float, float]) -> None:
    """Drop what a viewport does not show; cut lines that cross its edge."""
    from shapely import LineString, clip_by_rect

    left, top, right, bottom = clip
    keep = []
    columns = builder.columns
    for row in range(start, len(columns["kind"])):
        minx, miny = columns["minx"][row], columns["miny"][row]
        maxx, maxy = columns["maxx"][row], columns["maxy"][row]
        if minx is None or maxx < left or minx > right or maxy < top or miny > bottom:
            continue
        points = columns["points"][row]
        inside = minx >= left and maxx <= right and miny >= top and maxy <= bottom
        if not inside and columns["kind"][row] in ("line", "polyline") and len(points) >= 4:
            cut = clip_by_rect(
                LineString(list(zip(points[0::2], points[1::2], strict=True))),
                left,
                top,
                right,
                bottom,
            )
            if cut.is_empty or cut.geom_type != "LineString":
                continue
            flat = [value for point in cut.coords for value in point]
            columns["points"][row] = flat
            columns["minx"][row], columns["maxx"][row] = min(flat[0::2]), max(flat[0::2])
            columns["miny"][row], columns["maxy"][row] = min(flat[1::2]), max(flat[1::2])
        keep.append(row)
    for name, values in columns.items():
        columns[name] = values[:start] + [values[row] for row in keep]


def _style(entity: Any) -> dict[str, Any]:
    from ezdxf import colors

    color = -1
    true_color = entity.dxf.get("true_color")
    if true_color is not None:
        color = int(true_color)
    else:
        aci = entity.dxf.get("color", 256)
        if 0 < aci < 256:
            red, green, blue = colors.aci2rgb(aci)
            color = (red << 16) | (green << 8) | blue
    weight = entity.dxf.get("lineweight", -1)
    return {
        "layer": entity.dxf.get("layer"),
        "color": color,
        "linetype": entity.dxf.get("linetype"),
        "lineweight": weight / 100 if isinstance(weight, int) and weight >= 0 else None,
    }


def _entities(
    layout: Any,
    placement: Placement,
    builder: Builder,
    *,
    skip_viewports: bool = False,
    depth: int = 0,
    group: int | None = None,
) -> None:
    for entity in layout:
        _entity(entity, placement, builder, depth=depth, group=group, skip_viewports=skip_viewports)


def _entity(
    entity: Any,
    placement: Placement,
    builder: Builder,
    *,
    depth: int,
    group: int | None,
    skip_viewports: bool = False,
) -> None:
    kind = entity.dxftype()
    style = _style(entity)
    own = group if group is not None else builder.group()
    at = placement.to_sheet

    if kind == "LINE":
        (x0, y0), (x1, y1) = (
            at(entity.dxf.start.x, entity.dxf.start.y),
            at(entity.dxf.end.x, entity.dxf.end.y),
        )
        builder.line(x0, y0, x1, y1, own, **style)
    elif kind == "CIRCLE":
        cx, cy = at(entity.dxf.center.x, entity.dxf.center.y)
        builder.circle(cx, cy, entity.dxf.radius * placement.scale, own, **style)
    elif kind == "ARC":
        cx, cy = at(entity.dxf.center.x, entity.dxf.center.y)
        builder.arc(
            cx,
            cy,
            entity.dxf.radius * placement.scale,
            entity.dxf.start_angle,
            entity.dxf.end_angle,
            own,
            **style,
        )
    elif kind in ("TEXT", "MTEXT", "ATTRIB"):
        _text(entity, placement, builder, own, style)
    elif kind == "INSERT":
        _insert(entity, placement, builder, depth, style)
    elif kind in ("DIMENSION", "ARC_DIMENSION"):
        _dimension(entity, placement, builder, own, style)
    elif kind == "HATCH":
        for path in _paths(entity):
            builder.hatch([value for point in path for value in at(*point)], own, **style)
    elif kind == "VIEWPORT" and skip_viewports:
        return
    elif kind in ("LWPOLYLINE", "POLYLINE", "SPLINE", "ELLIPSE"):
        closed = bool(getattr(entity, "closed", False) or entity.dxf.get("flags", 0) & 1)
        for path in _paths(entity):
            flat = [value for point in path for value in at(*point)]
            builder.polyline(flat, own, closed=closed, **style)


def _paths(entity: Any) -> list[list[tuple[float, float]]]:
    """An entity's outline as point lists in drawing units, curves flattened."""
    from ezdxf import path as dxf_path

    made: list[Any] = (
        list(dxf_path.from_hatch(entity))
        if entity.dxftype() == "HATCH"
        else [dxf_path.make_path(entity)]
    )
    result = []
    for single in made:
        points = [(float(point.x), float(point.y)) for point in single.flattening(FLATTEN_DISTANCE)]
        if len(points) >= 2:
            result.append(points)
    return result


def _text(
    entity: Any, placement: Placement, builder: Builder, group: int, style: dict[str, Any]
) -> None:
    if entity.dxftype() == "MTEXT":
        content = entity.plain_text()
        height = float(entity.dxf.get("char_height", 2.5))
    else:
        content = entity.dxf.text
        height = float(entity.dxf.get("height", 2.5))
    rotation = float(entity.dxf.get("rotation", 0.0))
    x, y = placement.to_sheet(entity.dxf.insert.x, entity.dxf.insert.y)
    sheet_height = height * placement.scale
    for number, line in enumerate(content.splitlines() or [""]):
        line = line.strip()
        if not line:
            continue
        # A DXF gives the baseline, not a box; estimate the box a proportional font fills.
        baseline = y + number * sheet_height * 1.6
        width = 0.75 * sheet_height * len(line)
        angle = math.radians(rotation)
        corners = [(0.0, 0.0), (width, 0.0), (width, sheet_height), (0.0, sheet_height)]
        xs = [x + cx * math.cos(angle) + cy * math.sin(angle) for cx, cy in corners]
        ys = [baseline - cx * math.sin(angle) - cy * math.cos(angle) for cx, cy in corners]
        builder.text(
            line,
            (min(xs), min(ys), max(xs), max(ys)),
            group,
            height=sheet_height,
            rotation=rotation,
            **style,
        )


def _insert(
    entity: Any, placement: Placement, builder: Builder, depth: int, style: dict[str, Any]
) -> None:
    from ezdxf import bbox

    group = builder.group()
    x, y = placement.to_sheet(entity.dxf.insert.x, entity.dxf.insert.y)
    extents = bbox.extents([entity], fast=True)
    if extents.has_data:
        (x0, y1), (x1, y0) = (
            placement.to_sheet(extents.extmin.x, extents.extmin.y),
            (placement.to_sheet(extents.extmax.x, extents.extmax.y)),
        )
        box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    else:
        box = (x, y, x, y)
    builder.insert(
        entity.dxf.name,
        x,
        y,
        group,
        box=box,
        rotation=float(entity.dxf.get("rotation", 0.0)),
        **style,
    )
    if depth >= MAX_INSERT_DEPTH:
        return
    for part in entity.virtual_entities():
        _entity(part, placement, builder, depth=depth + 1, group=group)


def _dimension(
    entity: Any, placement: Placement, builder: Builder, group: int, style: dict[str, Any]
) -> None:
    """A dimension's two measured points and its value: the evidence a scale is checked on."""
    try:
        measured = float(entity.get_measurement())
    except Exception:
        return
    first, second = entity.dxf.get("defpoint2"), entity.dxf.get("defpoint3")
    if first is None or second is None:
        return
    x0, y0 = placement.to_sheet(first.x, first.y)
    x1, y1 = placement.to_sheet(second.x, second.y)
    override = entity.dxf.get("text", "")
    label = override if override and override != "<>" else f"{measured:g}"
    builder.dimension(x0, y0, x1, y1, group, value=measured, text=label, **style)
