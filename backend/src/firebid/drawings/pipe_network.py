"""Pipework as a connected network: runs between junctions, symbols on it (FR-VIS-03).

1. **Candidates.** Pipe is told from other line work by what the consultant draws it with:
   the layer (DXF) or stroke colour (PDF). A consultant profile may name them. Otherwise
   they are learnt from the sheet itself: whatever most of the lines touching the installed
   sprinklers and valves are drawn on or with. Symbol geometry, legends and title blocks
   are never pipe.
2. **Noding.** Endpoints closer than the snap tolerance (paper mm, from the view's scale)
   are one node. An endpoint inside a symbol joins that symbol's node (pipe stops at a
   valve's body). A symbol sitting on a line, as a sprinkler sits on its branch, splits the
   line there. An endpoint on another line's interior is a tee, and splits that line. Lines
   that merely cross are not joined: in a plan they are usually at different heights.
3. **Topology.** Only connected components that reach at least one installed symbol are
   kept: a stray line of the right colour is not pipe.
4. **Runs.** Maximal chains of edges between stops. A stop is a junction of three or more,
   an open end, or a valve, fitting or riser. Sprinklers pass a run through: a branch is
   one run carrying its heads.

Pure: geometry and placed symbols in, a network out, with every edge's geometry rows kept
as its evidence.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
import numpy as np
import pyarrow as pa

from firebid.drawings.geometry import Kind, segments

# How far apart, on paper, two endpoints may be and still be one joint.
SNAP_MM = 0.6
# Lines shorter than this on paper are ticks, not pipe.
MIN_PIPE_MM = 0.5
# Two directions within this many degrees continue straight through a joint.
STRAIGHT_DEGREES = 10.0

SYMBOL_NODE_KINDS = {
    "sprinkler": "sprinkler",
    "valve": "valve",
    "device": "valve",
    "fitting": "fitting",
    "pipe": "riser",
}


@dataclass(frozen=True)
class Placed:
    """An installed symbol with the object type a person confirmed for it."""

    object_type: str
    category: str
    measure: str
    cx: float
    cy: float
    box: tuple[float, float, float, float]
    rotation: float | None = None
    method: str = "shape"
    match_distance: float = 0.0
    tolerance: float = 0.07
    attributes: dict[str, Any] = field(default_factory=dict)
    rows: tuple[int, ...] = ()
    description: str | None = None
    instance_id: int | None = None

    @property
    def radius(self) -> float:
        return max(self.box[2] - self.box[0], self.box[3] - self.box[1]) / 2


@dataclass(frozen=True)
class Profile:
    """Which line work is pipe, for one consultant. Empty means learn it from the sheet."""

    layers: tuple[str, ...] = ()
    colours: tuple[int, ...] = ()


@dataclass
class Edge:
    id: int
    a: int
    b: int
    points: tuple[tuple[float, float], tuple[float, float]]
    rows: tuple[int, ...]

    @property
    def length(self) -> float:
        (x0, y0), (x1, y1) = self.points
        return math.hypot(x1 - x0, y1 - y0)

    def direction_from(self, node: int) -> tuple[float, float]:
        (x0, y0), (x1, y1) = self.points if node == self.a else self.points[::-1]
        size = math.hypot(x1 - x0, y1 - y0) or 1.0
        return ((x1 - x0) / size, (y1 - y0) / size)


@dataclass
class Run:
    id: int
    edges: list[int]
    nodes: list[int]  # in order along the run
    sprinklers: list[int] = field(default_factory=list)  # symbol indexes on it

    def length(self, network: Network) -> float:
        return sum(network.edges[e].length for e in self.edges)


@dataclass
class Network:
    graph: nx.MultiGraph[int]
    edges: dict[int, Edge]
    node_kind: dict[int, str]  # junction | end | sprinkler | valve | fitting | riser
    node_symbol: dict[int, int]  # node -> index into placed
    node_xy: dict[int, tuple[float, float]]
    runs: list[Run]
    pipe_key: str | None  # the layer or colour pipe was taken from
    pipe_key_learnt: bool

    def symbol_node(self, index: int) -> int | None:
        return next((n for n, s in self.node_symbol.items() if s == index), None)


def _key_of(layer: Any, colour: Any, cad: bool) -> str | None:
    if cad:
        return f"layer:{layer}" if layer else None
    return f"colour:{colour}" if colour is not None and colour != -1 else None


def build(
    table: pa.Table,
    placed: list[Placed],
    *,
    snap_mm: float = SNAP_MM,
    excluded: list[tuple[float, float, float, float]] | None = None,
    profile: Profile | None = None,
) -> Network:
    lines = segments(table, kinds=(Kind.LINE, Kind.POLYLINE, Kind.ARC))
    kinds = np.asarray(table.column("kind").to_pylist(), dtype=object)
    groups = np.asarray(table.column("group").to_pylist(), dtype=object)
    colours = np.asarray(table.column("color").to_pylist(), dtype=object)
    methods = np.asarray(table.column("method").to_pylist(), dtype=object)
    cad = bool(len(methods)) and methods[0] == "cad_entity"
    insert_groups = set(groups[kinds == str(Kind.INSERT)].tolist())
    # A symbol's own strokes are never pipe, whether it came from a block or a PDF cluster.
    symbol_rows = {row for symbol in placed for row in symbol.rows}

    keep: list[int] = []
    # Once, not per segment: `lengths` computes every segment's length each time it is read.
    lengths = lines.lengths
    for i in range(len(lines)):
        row = int(lines.row[i])
        if groups[row] in insert_groups or row in symbol_rows or lengths[i] < MIN_PIPE_MM:
            continue
        mid = ((lines.x0[i] + lines.x1[i]) / 2, (lines.y0[i] + lines.y1[i]) / 2)
        if excluded and any(_inside(mid, box) for box in excluded):
            continue
        keep.append(i)

    def key(i: int) -> str | None:
        row = int(lines.row[i])
        return _key_of(lines.layer[i], colours[row], cad)

    wanted, learnt = _pipe_keys(keep, key, lines, placed, snap_mm, profile, cad)
    pieces = [
        (
            (float(lines.x0[i]), float(lines.y0[i])),
            (float(lines.x1[i]), float(lines.y1[i])),
            int(lines.row[i]),
        )
        for i in keep
        if key(i) in wanted
    ]
    network = _node(pieces, placed, snap_mm)
    network.pipe_key = ",".join(sorted(wanted)) or None
    network.pipe_key_learnt = learnt
    return network


def _inside(point: tuple[float, float], box: tuple[float, float, float, float]) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def _pipe_keys(
    keep: list[int],
    key: Any,
    lines: Any,
    placed: list[Placed],
    snap_mm: float,
    profile: Profile | None,
    cad: bool,
) -> tuple[set[str], bool]:
    """The layers or colours pipe is drawn with: from the profile, else from the sheet."""
    if profile is not None:
        named = (
            {f"layer:{layer}" for layer in profile.layers}
            if cad
            else {f"colour:{colour}" for colour in profile.colours}
        )
        if named:
            return named, False
    anchors = [p for p in placed if p.category in ("sprinkler", "valve", "fitting")]
    votes: Counter[str] = Counter()
    for i in keep:
        k = key(i)
        if k is None:
            continue
        a = (float(lines.x0[i]), float(lines.y0[i]))
        b = (float(lines.x1[i]), float(lines.y1[i]))
        for symbol in anchors:
            reach = symbol.radius + snap_mm
            if _distance_to_segment((symbol.cx, symbol.cy), a, b) <= reach:
                votes[k] += 1
                break
    if not votes:
        return set(), True
    return {votes.most_common(1)[0][0]}, True


def _distance_to_segment(
    point: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    return math.dist(point, _project(point, a, b)[0])


def _project(
    point: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> tuple[tuple[float, float], float]:
    """The nearest point on a-b to `point`, and how far along (0 to 1) it is."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    size = dx * dx + dy * dy
    if size == 0:
        return a, 0.0
    t = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / size))
    return (a[0] + t * dx, a[1] + t * dy), t


def _node(
    pieces: list[tuple[tuple[float, float], tuple[float, float], int]],
    placed: list[Placed],
    snap_mm: float,
) -> Network:
    node_xy: dict[int, tuple[float, float]] = {}
    node_kind: dict[int, str] = {}
    node_symbol: dict[int, int] = {}
    counter = iter(range(10**9))

    symbol_nodes: dict[int, int] = {}
    for index, symbol in enumerate(placed):
        kind = SYMBOL_NODE_KINDS.get(symbol.category)
        if kind is None or symbol.measure == "none":
            continue
        node = next(counter)
        node_xy[node], node_kind[node], node_symbol[node] = (symbol.cx, symbol.cy), kind, index
        symbol_nodes[index] = node

    def symbol_at(point: tuple[float, float]) -> int | None:
        for index, node in symbol_nodes.items():
            box = placed[index].box
            if (
                box[0] - snap_mm <= point[0] <= box[2] + snap_mm
                and box[1] - snap_mm <= point[1] <= box[3] + snap_mm
            ):
                return node
        return None

    # 1. Split each piece where a symbol sits on it (a sprinkler on its branch), and where
    # another piece's endpoint meets its interior (a tee).
    endpoints = [p for a, b, _ in pieces for p in (a, b)]
    split: list[tuple[tuple[float, float], tuple[float, float], int]] = []
    for a, b, row in pieces:
        cuts: list[tuple[float, tuple[float, float], int | None]] = []
        for index, node in symbol_nodes.items():
            symbol = placed[index]
            foot, t = _project((symbol.cx, symbol.cy), a, b)
            if 0.0 < t < 1.0 and math.dist(foot, (symbol.cx, symbol.cy)) <= symbol.radius * 0.5:
                cuts.append((t, foot, node))  # on the line, where the symbol sits on it
        for point in endpoints:
            foot, t = _project(point, a, b)
            if (
                0.0 < t < 1.0
                and math.dist(foot, point) <= snap_mm
                and (math.dist(point, a) > snap_mm and math.dist(point, b) > snap_mm)
            ):
                cuts.append((t, foot, None))
        cuts.sort(key=lambda cut: cut[0])
        previous = a
        for _, point, _node_id in cuts:
            if math.dist(previous, point) > 1e-9:
                split.append((previous, point, row))
            previous = point
        if math.dist(previous, b) > 1e-9:
            split.append((previous, b, row))

    # 2. Endpoints: into a symbol's node, or clustered into a joint.
    joints: list[tuple[float, float, int]] = []

    def node_for(point: tuple[float, float]) -> int:
        at_symbol = symbol_at(point)
        if at_symbol is not None:
            return at_symbol
        for x, y, node in joints:
            if math.dist((x, y), point) <= snap_mm:
                return node
        node = next(counter)
        joints.append((point[0], point[1], node))
        node_xy[node], node_kind[node] = point, "joint"
        return node

    graph: nx.MultiGraph[int] = nx.MultiGraph()
    edges: dict[int, Edge] = {}
    for a, b, row in split:
        u, v = node_for(a), node_for(b)
        if u == v:
            continue
        edge = Edge(len(edges), u, v, (a, b), (row,))
        edges[edge.id] = edge
        graph.add_edge(u, v, key=edge.id)
    for node in node_xy:
        if node in graph:
            graph.nodes[node]["kind"] = node_kind[node]

    # 3. Only what reaches an installed symbol is pipe.
    for component in list(nx.connected_components(graph)):
        if not any(node in node_symbol for node in component):
            for _u, _v, k in list(graph.edges(component, keys=True)):
                edges.pop(k, None)
            graph.remove_nodes_from(component)
    for node in list(graph.nodes):
        if node_kind.get(node) == "joint":
            node_kind[node] = (
                "junction"
                if graph.degree(node) >= 3
                else ("end" if graph.degree(node) == 1 else "joint")
            )

    network = Network(graph, edges, node_kind, node_symbol, node_xy, [], None, False)
    network.runs = _runs(network)
    return network


def _passes_through(network: Network, node: int) -> bool:
    kind = network.node_kind.get(node)
    return network.graph.degree(node) == 2 and kind in ("joint", "sprinkler")


def _runs(network: Network) -> list[Run]:
    graph, edges = network.graph, network.edges
    seen: set[int] = set()
    runs: list[Run] = []
    for start in sorted(edges):
        if start in seen:
            continue
        chain = [start]
        seen.add(start)
        # Walk both ways from the edge while the node passes a run through.
        ends = [edges[start].a, edges[start].b]
        for side in (0, 1):
            node, edge_id = ends[side], start
            while _passes_through(network, node):
                nxt = next(k for _, _, k in graph.edges(node, keys=True) if k != edge_id)
                if nxt in seen:
                    break
                seen.add(nxt)
                if side == 0:
                    chain.insert(0, nxt)
                else:
                    chain.append(nxt)
                other = edges[nxt].b if edges[nxt].a == node else edges[nxt].a
                node, edge_id = other, nxt
            ends[side] = node
        nodes = _ordered_nodes(edges, chain, ends[0])
        run = Run(len(runs), chain, nodes)
        run.sprinklers = [
            network.node_symbol[n] for n in nodes if network.node_kind.get(n) == "sprinkler"
        ]
        runs.append(run)
    return runs


def _ordered_nodes(edges: dict[int, Edge], chain: list[int], first: int) -> list[int]:
    nodes = [first]
    for edge_id in chain:
        edge = edges[edge_id]
        nodes.append(edge.b if edge.a == nodes[-1] else edge.a)
    return nodes


def straight(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """Whether two directions leaving one joint continue each other (180 degrees apart)."""
    dot = a[0] * b[0] + a[1] * b[1]
    return dot <= -math.cos(math.radians(STRAIGHT_DEGREES))
