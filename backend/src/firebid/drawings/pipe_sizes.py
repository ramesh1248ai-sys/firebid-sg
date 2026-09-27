"""Pipe sizes: annotations read, attached to runs, and carried along them (FR-VIS-06).

* **Reading.** `DN150`, `150Ø`, `Ø65`, `150mm`, `150 dia`, `6"` and `6" dia` all state a
  nominal size, read as DN. Inch sizes use the standard DN equivalents.
* **Attaching.** A label belongs to the nearest run that runs the same way (within 10
  degrees of the text's direction), within a few text heights of it and alongside it, not
  beyond its ends. Its confidence falls with distance.
* **Carrying.** A size holds along a *size group*: runs joined through elbows, straight on
  through tees and valves, but never across a reducer. A tee's branch leg is its own group.
* **Disagreeing.** Two labels on one size group that say different sizes are a conflict: the
  group gets no size and says why. Nothing is guessed.

Pure: a network and text spans in, sizes per run out.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from firebid.drawings.pipe_network import Network, straight

# Inch sizes and the DN they are sold as.
INCH_DN = {
    "1/2": 15,
    "3/4": 20,
    "1": 25,
    "1-1/4": 32,
    "1 1/4": 32,
    "1-1/2": 40,
    "1 1/2": 40,
    "2": 50,
    "2-1/2": 65,
    "2 1/2": 65,
    "3": 80,
    "4": 100,
    "5": 125,
    "6": 150,
    "8": 200,
    "10": 250,
    "12": 300,
}
NOMINAL = {15, 20, 25, 32, 40, 50, 65, 80, 100, 125, 150, 200, 250, 300}
_DN = re.compile(r"^\s*(?:DN\s*(\d{2,3})|(\d{2,3})\s*(?:Ø|MM|DIA\.?)|Ø\s*(\d{2,3}))\s*$", re.I)
_INCH = re.compile(r'^\s*(\d(?:[- ]\d/\d)?|\d/\d)\s*(?:"|IN\b|INCH)\s*(?:DIA\.?)?\s*$', re.I)
# A label is at most this many text heights from its run, measured across it.
REACH_HEIGHTS = 4.0
ANGLE_DEGREES = 10.0


def parse(text: str) -> int | None:
    """The nominal DN a size annotation states, or None if it is not one."""
    match = _DN.match(text.replace("⌀", "Ø").replace("ø", "Ø"))
    if match:
        value = int(next(group for group in match.groups() if group))
        return value if value in NOMINAL else None
    match = _INCH.match(text)
    if match:
        return INCH_DN.get(match.group(1))
    return None


@dataclass(frozen=True)
class Label:
    text: str
    dn: int
    x: float
    y: float
    run: int
    distance: float  # paper mm across the run
    confidence: float


@dataclass
class RunSize:
    dn: int | None
    status: str  # labelled | propagated | conflict | unknown
    labels: list[Label] = field(default_factory=list)  # every label on its size group
    group: int = -1
    reason: str = ""


def attach(network: Network, spans: list[dict[str, Any]]) -> list[Label]:
    """Every size label on the sheet, each attached to the run it describes."""
    found = []
    for span in spans:
        dn = parse(str(span.get("text") or ""))
        if dn is None:
            continue
        cx, cy = (span["minx"] + span["maxx"]) / 2, (span["miny"] + span["maxy"]) / 2
        height = max(float(span.get("height") or 0.0), 0.5)
        if span.get("rotation") in (None, 0) and span["maxy"] - span["miny"] > 2 * (
            span["maxx"] - span["minx"]
        ):
            angle = 90.0  # a box taller than it is wide: vertical text with no rotation read
        else:
            angle = float(span.get("rotation") or 0.0)
        # Rotated text's box height is its length; the text height is the short side.
        height = min(height, span["maxx"] - span["minx"], span["maxy"] - span["miny"]) or height
        best: tuple[float, int] | None = None
        for run in network.runs:
            for edge_id in run.edges:
                edge = network.edges[edge_id]
                (x0, y0), (x1, y1) = edge.points
                edge_angle = math.degrees(math.atan2(y1 - y0, x1 - x0))
                if _angle_gap(edge_angle, angle) > ANGLE_DEGREES:
                    continue
                across, along = _offsets((cx, cy), edge.points)
                if along < -0.1 or along > 1.1 or across > REACH_HEIGHTS * height:
                    continue
                if best is None or across < best[0]:
                    best = (across, run.id)
        if best is None:
            continue
        across, run_id = best
        confidence = max(0.3, 1.0 - across / (REACH_HEIGHTS * height) * 0.6)
        found.append(
            Label(
                str(span["text"]).strip(),
                dn,
                cx,
                cy,
                run_id,
                round(across, 3),
                round(confidence, 3),
            )
        )
    return found


def _angle_gap(a: float, b: float) -> float:
    gap = abs((a - b) % 180.0)
    return min(gap, 180.0 - gap)


def _offsets(point: tuple[float, float], points: Any) -> tuple[float, float]:
    """Distance across the line through `points`, and how far along (0 to 1) the foot is."""
    (x0, y0), (x1, y1) = points
    dx, dy = x1 - x0, y1 - y0
    size = math.hypot(dx, dy) or 1.0
    along = ((point[0] - x0) * dx + (point[1] - y0) * dy) / (size * size)
    across = abs((point[0] - x0) * dy - (point[1] - y0) * dx) / size
    return across, along


def size_groups(network: Network, reducers: set[int]) -> list[set[int]]:
    """Runs that must share one size: joined at elbows, straight through tees and valves.

    `reducers` are the fitting nodes that change size: nothing is carried across them.
    """
    parent = {run.id: run.id for run in network.runs}

    def root(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    at: dict[int, list[tuple[int, tuple[float, float]]]] = {}
    for run in network.runs:
        for end, edge_id in ((run.nodes[0], run.edges[0]), (run.nodes[-1], run.edges[-1])):
            at.setdefault(end, []).append((run.id, network.edges[edge_id].direction_from(end)))
    for node, legs in at.items():
        if node in reducers or len(legs) < 2:
            continue
        kind = network.node_kind.get(node)
        if len(legs) == 2 and kind in ("joint", "valve", "fitting", "sprinkler"):
            parent[root(legs[0][0])] = root(legs[1][0])  # an elbow or an in-line fitting
            continue
        # A tee: only the legs that continue each other share a size.
        for i, (run_a, dir_a) in enumerate(legs):
            for run_b, dir_b in legs[i + 1 :]:
                if straight(dir_a, dir_b):
                    parent[root(run_a)] = root(run_b)
    groups: dict[int, set[int]] = {}
    for run in network.runs:
        groups.setdefault(root(run.id), set()).add(run.id)
    return list(groups.values())


def assign(network: Network, labels: list[Label], reducers: set[int]) -> dict[int, RunSize]:
    """Each run's size, carried from the labels on its size group."""
    sizes: dict[int, RunSize] = {}
    for index, group in enumerate(size_groups(network, reducers)):
        on_group = [label for label in labels if label.run in group]
        stated = {label.dn for label in on_group}
        for run_id in group:
            own = [label for label in on_group if label.run == run_id]
            if len(stated) > 1:
                sizes[run_id] = RunSize(
                    None,
                    "conflict",
                    on_group,
                    index,
                    "annotations on this run disagree: "
                    + ", ".join(sorted({label.text for label in on_group})),
                )
            elif stated:
                dn = next(iter(stated))
                sizes[run_id] = RunSize(dn, "labelled" if own else "propagated", on_group, index)
            else:
                sizes[run_id] = RunSize(None, "unknown", [], index, "no size annotation found")
    return sizes
