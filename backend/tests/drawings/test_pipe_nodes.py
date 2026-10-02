"""Joining pipe into a network: the same network, without trying every pair (NFR-01).

`_node` tried every piece against every endpoint and every symbol, and every endpoint
against every joint so far: 57 million pairs, and a minute, on one real sheet. It now rules
out on arrays the pairs that cannot meet and decides the rest as before. The loop it
replaced is kept here, as written, as the reference: the two must give the same network.
"""

from __future__ import annotations

import math

import networkx as nx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from firebid.drawings.pipe_network import (
    SNAP_MM,
    SYMBOL_NODE_KINDS,
    Edge,
    Network,
    Placed,
    _node,
    _project,
    _runs,
)

pytestmark = pytest.mark.req("NFR-01")

Point = tuple[float, float]
Piece = tuple[Point, Point, int]


def reference(pieces: list[Piece], placed: list[Placed], snap_mm: float) -> Network:
    node_xy: dict[int, Point] = {}
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

    def symbol_at(point: Point) -> int | None:
        for index, node in symbol_nodes.items():
            box = placed[index].box
            if (
                box[0] - snap_mm <= point[0] <= box[2] + snap_mm
                and box[1] - snap_mm <= point[1] <= box[3] + snap_mm
            ):
                return node
        return None

    endpoints = [p for a, b, _ in pieces for p in (a, b)]
    split: list[Piece] = []
    for a, b, row in pieces:
        cuts: list[tuple[float, Point, int | None]] = []
        for index, node in symbol_nodes.items():
            symbol = placed[index]
            foot, t = _project((symbol.cx, symbol.cy), a, b)
            if 0.0 < t < 1.0 and math.dist(foot, (symbol.cx, symbol.cy)) <= symbol.radius * 0.5:
                cuts.append((t, foot, node))
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

    joints: list[tuple[float, float, int]] = []

    def node_for(point: Point) -> int:
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


def told(network: Network) -> tuple[object, ...]:
    """Everything a network says, in the order it says it."""
    return (
        [(e.id, e.a, e.b, e.points, e.rows) for e in network.edges.values()],
        list(network.node_kind.items()),
        list(network.node_symbol.items()),
        list(network.node_xy.items()),
        [(run.id, run.edges, run.nodes, run.sprinklers) for run in network.runs],
        sorted(network.graph.edges(keys=True)),
    )


def symbol(x: float, y: float, category: str = "sprinkler", half: float = 1.0) -> Placed:
    return Placed(
        object_type=category,
        category=category,
        measure="count",
        cx=x,
        cy=y,
        box=(x - half, y - half, x + half, y + half),
    )


# A coarse grid with a little jitter: many endpoints land on other pieces, on symbols and
# within a snap of each other, which is where the two could differ.
step = st.integers(min_value=0, max_value=10).map(lambda n: n * 5.0)
jitter = st.sampled_from([0.0, 0.0, 0.3, SNAP_MM, -0.59, 0.61, 1.2])
point = st.tuples(st.tuples(step, jitter).map(sum), st.tuples(step, jitter).map(sum))
placed_symbol = st.tuples(point, st.sampled_from(["sprinkler", "valve", "pipe", "text"]))


@given(st.lists(st.tuples(point, point), max_size=40), st.lists(placed_symbol, max_size=12))
@settings(max_examples=300, deadline=None)
def test_the_network_is_the_reference_s(
    lines: list[tuple[Point, Point]], symbols: list[tuple[Point, str]]
) -> None:
    pieces = [(a, b, row) for row, (a, b) in enumerate(lines)]
    placed = [symbol(x, y, category) for (x, y), category in symbols]
    assert told(_node(pieces, placed, SNAP_MM)) == told(reference(pieces, placed, SNAP_MM))


def test_a_branch_is_cut_at_its_heads_and_at_a_tee() -> None:
    pieces: list[Piece] = [((0.0, 0.0), (30.0, 0.0), 0), ((15.0, 0.0), (15.0, 20.0), 1)]
    placed = [symbol(10.0, 0.0), symbol(20.0, 0.0), symbol(15.0, 20.0)]
    network = _node(pieces, placed, SNAP_MM)
    assert told(network) == told(reference(pieces, placed, SNAP_MM))
    assert sorted(network.node_kind.values()).count("junction") == 1
    assert len(network.edges) == 5


def test_no_pipe_no_network() -> None:
    network = _node([], [symbol(1.0, 1.0)], SNAP_MM)
    assert not network.edges and not network.runs
