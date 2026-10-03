"""Detections: every installed object and pipe run on a sheet, as proposals (FR-VIS-03/04/09).

From the confirmed symbols and the pipe network, one sheet yields:

* **objects**: sprinklers, valves, devices, fittings and equipment (pumps, tanks, breeching
  inlets, hydrants, hose reels, landing valves), one per installed symbol, with its
  orientation where the symbol shows one, and for equipment the tag written beside it;
* **risers**: a riser symbol is a vertical pipe the plan cannot show the length of;
* **drops**: one at every sprinkler on the network, "vertical, not drawn", for P1-07's rules
  to give a length;
* **runs**: each pipe run with its class (main or branch), size and length, and the system
  it belongs to where what stands on its pipework says (`equipment.system_of`).

Every detection carries where it is (view, grid reference, level, sheet position), how it
was found (method), what it was found from (geometry rows, symbol instance, labels), the
features its confidence is computed from, and a raw confidence. The calibrated confidence
is applied afterwards (`firebid.evals.calibration`), because it is fitted on outcomes.

A length is given only for a run in a view whose scale is verified or calibrated (FR-VIS-05);
elsewhere the run carries its paper length and no length.

Pure: geometry, confirmed symbols and views in, detections out.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa

from firebid.drawings import equipment, pipe_sizes
from firebid.drawings.geometry import texts
from firebid.drawings.grids import GridSystem
from firebid.drawings.pipe_network import Network, Placed, Profile, build

# Bump when detection changes what it produces.
DETECTOR_VERSION = "2"
# A symbol found only by vision never starts above this (P1-05 build item 6).
VISION_CAP = 0.5
# A sprinkler or valve off the pipework is doubtful. Equipment is less so: a tank, a pump
# controller or an air compressor is often drawn with no pipe to it.
OFF_NETWORK = 0.55
EQUIPMENT_OFF_NETWORK = 0.85
# Views that are of no one level.
NOT_PLANS = ("schematic", "section", "elevation", "detail")


@dataclass(frozen=True)
class ViewInfo:
    """What detection needs of a view: where it is, its scale, its grid and level."""

    id: Any
    extent: tuple[float, float, float, float]
    denominator: float | None  # set only when the view is measurable
    grid: GridSystem | None = None
    level: str | None = None
    kind: str | None = None  # plan | enlarged plan | schematic | section | ...

    def contains(self, x: float, y: float) -> bool:
        return self.extent[0] <= x <= self.extent[2] and self.extent[1] <= y <= self.extent[3]


@dataclass
class Detected:
    kind: str  # object | riser | drop
    object_type: str
    category: str
    method: str
    x: float
    y: float
    box: tuple[float, float, float, float]
    view: ViewInfo | None
    grid_reference: str | None
    level: str | None
    evidence: dict[str, Any]
    features: dict[str, float | str | bool]
    raw_confidence: float
    orientation: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    calibrated_confidence: float | None = None


@dataclass
class DetectedRun:
    run_id: int
    run_class: str  # main | branch
    dn: int | None
    size_status: str
    size_reason: str
    labels: list[pipe_sizes.Label]
    paper_length_mm: float
    length_mm: float | None
    view: ViewInfo | None
    grid_reference: str | None
    level: str | None
    points: list[list[float]]
    rows: list[int]
    features: dict[str, float | str | bool]
    raw_confidence: float
    calibrated_confidence: float | None = None
    system: str | None = None  # hydrant | hose_reel | rising_main | ... where the sheet says


@dataclass
class SheetDetections:
    objects: list[Detected]
    runs: list[DetectedRun]
    network: Network
    pipe_key: str | None


def _view_at(views: list[ViewInfo], x: float, y: float) -> ViewInfo | None:
    containing = [view for view in views if view.contains(x, y)]
    return min(
        containing,
        key=lambda v: (v.extent[2] - v.extent[0]) * (v.extent[3] - v.extent[1]),
        default=None,
    )


def _where(views: list[ViewInfo], x: float, y: float) -> tuple[ViewInfo | None, str | None]:
    view = _view_at(views, x, y)
    grid = view.grid if view is not None else None
    return view, (grid.reference(x, y) if grid is not None else None)


def symbol_score(symbol: Placed, on_network: bool) -> tuple[float, dict[str, float | str | bool]]:
    """A symbol's raw confidence and the features it came from."""
    if symbol.method == "block_hash":
        match = 1.0
    elif symbol.method == "vision":
        match = VISION_CAP
    else:
        match = max(0.0, 1.0 - 0.5 * symbol.match_distance / max(symbol.tolerance, 1e-6))
    off = EQUIPMENT_OFF_NETWORK if symbol.category == "equipment" else OFF_NETWORK
    topology = 1.0 if on_network else off
    raw = match * topology
    if symbol.method == "vision":
        raw = min(raw, VISION_CAP)
    return round(raw, 4), {
        "method": symbol.method,
        "match_distance": round(symbol.match_distance, 4),
        "match_score": round(match, 4),
        "on_network": on_network,
    }


def run_score(
    size: pipe_sizes.RunSize, consistency: float = 1.0
) -> tuple[float, dict[str, float | str | bool]]:
    """A run's raw confidence: how its size was found, times how well it fits its network."""
    best = max((label.confidence for label in size.labels), default=0.0)
    # A size carried along its size group rests on the same annotation as the run it was
    # written on, so both score by that annotation; a doubtful one is caught by consistency.
    score = {
        "labelled": 0.6 + 0.4 * best,
        "propagated": 0.6 + 0.4 * best,
        "conflict": 0.15,
        "unknown": 0.25,
    }[size.status]
    return round(score * consistency, 4), {
        "size_status": size.status,
        "label_confidence": round(best, 3),
        "topology_consistency": round(consistency, 3),
    }


def consistency(network: Network, sizes: dict[int, pipe_sizes.RunSize]) -> dict[int, float]:
    """How plausible each branch's size is beside the rest of the network.

    Two checks a person makes at a glance: a branch sized unlike most of its sibling
    branches is suspect (often a mistyped label), and a branch bigger than the main that
    feeds it is almost certainly wrong. Mains are not checked this way: 1.0.
    """
    branches = [run for run in network.runs if run.sprinklers and sizes[run.id].dn]
    sized = [sizes[run.id].dn for run in branches]
    majority = max(set(sized), key=sized.count) if len(sized) >= 3 else None
    feeding: dict[int, int | None] = {}
    for run in branches:
        feeding[run.id] = None
        for end in (run.nodes[0], run.nodes[-1]):
            for other in network.runs:
                if (
                    other.id != run.id
                    and not other.sprinklers
                    and end in (other.nodes[0], other.nodes[-1])
                ):
                    feeding[run.id] = sizes[other.id].dn
    result = {run.id: 1.0 for run in network.runs}
    for run in branches:
        dn = sizes[run.id].dn
        factor = 1.0
        if majority is not None and dn != majority and sized.count(majority) > len(sized) / 2:
            factor *= 0.65
        main = feeding.get(run.id)
        if main is not None and dn is not None and dn > main:
            factor *= 0.5
        result[run.id] = factor
    return result


def detect(
    table: pa.Table,
    placed: list[Placed],
    views: list[ViewInfo],
    *,
    excluded: list[tuple[float, float, float, float]] | None = None,
    profile: Profile | None = None,
    snap_mm: float | None = None,
) -> SheetDetections:
    network = build(
        table,
        placed,
        excluded=excluded,
        profile=profile,
        **({"snap_mm": snap_mm} if snap_mm is not None else {}),
    )
    labels = pipe_sizes.attach(network, texts(table))
    reducers = {
        node
        for node, index in network.node_symbol.items()
        if placed[index].category == "fitting"
        and str(placed[index].attributes.get("fitting", "reducer")) == "reducer"
    }
    sizes = pipe_sizes.assign(network, labels, reducers)
    fits = consistency(network, sizes)

    run_of_sprinkler: dict[int, int] = {}
    for run in network.runs:
        for index in run.sprinklers:
            run_of_sprinkler[index] = run.id
    systems = _systems(network, placed)
    spans = texts(table)
    tagged = equipment.tags(
        [(symbol.cx, symbol.cy, 2 * symbol.radius) for symbol in placed],
        spans if any(symbol.category == "equipment" for symbol in placed) else [],
    )

    marks = equipment.level_marks(spans)

    objects: list[Detected] = []
    for index, symbol in enumerate(placed):
        if symbol.measure == "none":
            continue
        node = network.symbol_node(index)
        on_network = node is not None and node in network.graph
        raw, features = symbol_score(symbol, on_network)
        view, grid_reference = _where(views, symbol.cx, symbol.cy)
        evidence: dict[str, Any] = {
            "geometry_rows": list(symbol.rows),
            "symbol_instance_id": symbol.instance_id,
            "network_node": node if on_network else None,
            "description": symbol.description,
        }
        kind = "riser" if symbol.category == "pipe" else "object"
        attributes = dict(symbol.attributes)
        if kind == "riser":
            attributes.update({"run_class": "riser", "vertical_not_drawn": True})
            system = (
                systems.get(node) if on_network and node is not None else None
            ) or equipment.system_of(set(), set(), [symbol.description or ""])
            if system:
                attributes["system"] = system
        tag = tagged.get(index) if symbol.category == "equipment" else None
        if tag is not None:
            attributes["tag"] = tag.tag
            evidence["tag"] = {"text": tag.text, "distance_mm": tag.distance_mm}
        level = view.level if view else None
        if level is None and (view is None or view.kind in NOT_PLANS):
            # A schematic or section is of no one level: what it draws is on the level it
            # names nearest, where it names its levels.
            mark = equipment.level_at(marks, symbol.cy)
            if mark is not None:
                level = mark.level
                evidence["level_from"] = mark.text
        objects.append(
            Detected(
                kind=kind,
                object_type=symbol.object_type,
                category=symbol.category,
                method=_method(symbol),
                x=symbol.cx,
                y=symbol.cy,
                box=symbol.box,
                view=view,
                grid_reference=grid_reference,
                level=level,
                evidence=evidence,
                features=features,
                raw_confidence=raw,
                orientation=symbol.rotation,
                attributes=attributes,
            )
        )
        if symbol.category == "sprinkler" and on_network:
            run_id = run_of_sprinkler.get(index)
            size = sizes.get(run_id) if run_id is not None else None
            run_raw = (
                run_score(size, fits.get(run_id, 1.0))[0] if size and run_id is not None else 0.25
            )
            objects.append(
                Detected(
                    kind="drop",
                    object_type="pipe",
                    category="pipe",
                    method="topology",
                    x=symbol.cx,
                    y=symbol.cy,
                    box=symbol.box,
                    view=view,
                    grid_reference=grid_reference,
                    level=view.level if view else None,
                    evidence={"network_node": node, "run": run_id, "sprinkler": evidence},
                    features={"from_sprinkler": raw, "from_run": run_raw},
                    raw_confidence=round(min(raw, run_raw), 4),
                    attributes={
                        "run_class": "drop",
                        "vertical_not_drawn": True,
                        "nominal_diameter_mm": size.dn if size else None,
                    },
                )
            )

    runs: list[DetectedRun] = []
    for run in network.runs:
        size = sizes[run.id]
        points = [list(network.node_xy[node]) for node in run.nodes]
        middle = _middle(network, run)
        view, grid_reference = _where(views, *middle)
        paper = run.length(network)
        raw, features = run_score(size, fits[run.id])
        system = systems.get(run.nodes[0])
        if system:
            features["system"] = system
        runs.append(
            DetectedRun(
                run_id=run.id,
                run_class="branch" if run.sprinklers else "main",
                dn=size.dn,
                size_status=size.status,
                size_reason=size.reason,
                labels=size.labels,
                paper_length_mm=round(paper, 3),
                length_mm=round(paper * view.denominator, 1)
                if view is not None and view.denominator
                else None,
                view=view,
                grid_reference=grid_reference,
                level=view.level if view else None,
                points=[[round(x, 3), round(y, 3)] for x, y in points],
                rows=sorted({row for e in run.edges for row in network.edges[e].rows}),
                features=features,
                raw_confidence=raw,
                system=system,
            )
        )
    return SheetDetections(objects, runs, network, network.pipe_key)


def _systems(network: Network, placed: list[Placed]) -> dict[int, str]:
    """Each network node's system, for the nodes of pipework that says which it is."""
    import networkx as nx

    out: dict[int, str] = {}
    for component in nx.connected_components(network.graph):
        on_it = [placed[network.node_symbol[n]] for n in component if n in network.node_symbol]
        system = equipment.system_of(
            {symbol.object_type for symbol in on_it},
            {symbol.category for symbol in on_it},
            [symbol.description or "" for symbol in on_it if symbol.category == "pipe"],
        )
        if system:
            out.update(dict.fromkeys(component, system))
    return out


def _method(symbol: Placed) -> str:
    return {"block_hash": "cad_block", "shape": "pdf_shape", "vision": "vision"}.get(
        symbol.method, symbol.method
    )


def _middle(network: Network, run: Any) -> tuple[float, float]:
    """The point halfway along a run, for its grid reference."""
    half, walked = run.length(network) / 2, 0.0
    for edge_id in run.edges:
        edge = network.edges[edge_id]
        if walked + edge.length >= half:
            t = (half - walked) / (edge.length or 1.0)
            (x0, y0), (x1, y1) = edge.points
            return x0 + t * (x1 - x0), y0 + t * (y1 - y0)
        walked += edge.length
    return network.node_xy[run.nodes[0]]


def missing_fields(item: Detected | DetectedRun) -> list[str]:
    """What a detection lacks of what FR-VIS-09 requires of every one."""
    missing = []
    if isinstance(item, Detected) and not item.method:
        missing.append("method")
    evidence = item.evidence if isinstance(item, Detected) else {"rows": item.rows}
    if not evidence or not any(evidence.values()):
        missing.append("evidence")
    if item.view is None:
        missing.append("view")
    if item.grid_reference is None:
        missing.append("grid_reference")
    if item.calibrated_confidence is None or math.isnan(item.calibrated_confidence):
        missing.append("calibrated_confidence")
    return missing


@dataclass(frozen=True)
class TypeInfo:
    """What a confirmed mapping says a symbol is."""

    object_type: str
    category: str
    measure: str
    attributes: dict[str, Any] = field(default_factory=dict)


def placed_from_legend(
    table: pa.Table,
    page: tuple[float, float, float, float],
    type_of: Any,
) -> tuple[list[Placed], list[tuple[float, float, float, float]]]:
    """Installed symbols matched to the sheet's own legend, typed by `type_of(description)`.

    For the evaluation predictor and tests: in the platform, symbols come from P1-04's
    instances and a person's confirmed mappings. Rows `type_of` cannot type are left out,
    exactly as an unconfirmed mapping is. Returns the symbols and the regions (legends and
    title block) where nothing is installed.
    """
    from firebid.drawings import legends, symbols
    from firebid.drawings.views import _title_block_region

    found = legends.detect(table, page)
    region = _title_block_region(texts(table), page)
    excluded = [legend.box for legend in found]
    if region is not None:
        excluded.append((region.x0, region.y0, region.x1, region.y1))
    rows = [row for legend in found for row in legend.rows if row.symbol.signature]
    references = symbols.Candidates([row.symbol.signature for row in rows])  # type: ignore[misc]
    placed = []
    for cluster in symbols.clusters(table, excluding=excluded):
        if cluster.signature is None:
            continue
        match = symbols.best_match(cluster.signature, references)
        if match is None:
            continue
        row = rows[match.index]
        kind = type_of(row.description)
        if kind is None:
            continue
        reference = references[match.index]
        if reference is None:  # rows without a signature were left out above
            continue
        turned = cluster.rotation
        if turned is None:
            turned = symbols.orientation(cluster.signature, reference)
        cx, cy = cluster.centre
        placed.append(
            Placed(
                object_type=kind.object_type,
                category=kind.category,
                measure=kind.measure,
                cx=cx,
                cy=cy,
                box=cluster.box,
                rotation=turned,
                method=match.how,
                match_distance=match.distance,
                tolerance=reference.tolerance,
                attributes=dict(kind.attributes),
                rows=cluster.rows,
                description=row.description,
            )
        )
    return placed, excluded


def views_of(
    table: pa.Table,
    page: tuple[float, float, float, float],
    sheet_scale: str | None,
    source_views: list[dict[str, Any]] | None = None,
) -> list[ViewInfo]:
    """The sheet's views as detection needs them, from P1-03's analysis of its geometry."""
    from firebid.drawings import views

    return [
        ViewInfo(
            id=index,
            extent=item.view.extent,
            denominator=item.verdict.denominator if item.verdict.measurable else None,
            grid=item.grid,
            level=item.view.level,
            kind=str(item.view.kind),
        )
        for index, item in enumerate(views.analyse(table, page, sheet_scale, source_views))
    ]
