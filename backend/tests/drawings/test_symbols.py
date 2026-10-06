"""Symbol signatures and legends: a symbol matches its legend entry however it is placed.

Consultant Alpha's plan installs each legend symbol four ways: as drawn, turned 37 degrees,
turned 90 and enlarged 1.6 times, turned 211 and shrunk to 0.7. Every one must match its
legend row, in the DXF and in the PDF, where only line work survives. The plan's one
symbol that no legend explains must match nothing.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from firebid.drawings import geometry, legends, symbols
from firebid.drawings.geometry import Builder, Method
from firebid.evals import synthetic
from firebid.evals import synthetic_symbols as fixtures
from firebid.parsing import geometry_pdf
from firebid.parsing.geometry_dxf import extract as extract_dxf

pytestmark = pytest.mark.req("FR-VIS-02")
A3 = (0.0, 0.0, 420.0, 297.0)


def table_of(document: Any, form: str, folder: Path, name: str) -> Any:
    if form == "dxf":
        return geometry.from_parquet(extract_dxf(synthetic.dxf_bytes(document), None)["parquet"])
    path = synthetic.write_pdf(document, folder / f"{name}.pdf", live_text=True)
    return geometry.from_parquet(geometry_pdf.extract(path.read_bytes(), 0)["parquet"])


@pytest.fixture(scope="module")
def folder(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("symbols")


@pytest.fixture(scope="module")
def alpha_plan() -> Any:
    return fixtures.plan_sheet(fixtures.ALPHA, with_legend=False)


def truth_blocks(plan: Any) -> list[tuple[tuple[float, float], str]]:
    """Where each insert lands on the sheet, from the DXF, to judge the PDF by."""
    table = geometry.from_parquet(extract_dxf(synthetic.dxf_bytes(plan), None)["parquet"])
    return [(cluster.centre, str(cluster.block)) for cluster in symbols.clusters(table)]


def block_at(truth: list[tuple[tuple[float, float], str]], centre: tuple[float, float]) -> str:
    return min(truth, key=lambda item: math.dist(item[0], centre))[1]


class TestMatchingALegend:
    @pytest.mark.parametrize("form", ["dxf", "pdf"])
    def test_every_placed_symbol_matches_its_legend_entry_and_the_mystery_matches_none(
        self, form: str, folder: Path, alpha_plan: Any
    ) -> None:
        legend_table = table_of(fixtures.legend_sheet(fixtures.ALPHA), form, folder, "legend")
        [legend] = legends.detect(legend_table, A3)
        entries = [row.symbol.signature for row in legend.rows]
        assert all(entry is not None for entry in entries)
        types = {s.block: s.object_type for s in fixtures.ALPHA.symbols}
        described = {s.description: s.block for s in fixtures.ALPHA.symbols}

        plan, truth = alpha_plan
        placed = truth_blocks(plan)
        matched: dict[str, int] = {}
        unmatched: list[str] = []
        for cluster in symbols.clusters(table_of(plan, form, folder, "plan")):
            assert cluster.signature is not None
            found = symbols.best_match(cluster.signature, entries)  # type: ignore[arg-type]
            actually = block_at(placed, cluster.centre)
            if found is None:
                unmatched.append(actually)
                continue
            row = legend.rows[found.index]
            assert described[row.description] == actually, (form, actually, found)
            kind = types[actually]
            matched[kind] = matched.get(kind, 0) + 1

        assert matched == truth.counts
        assert unmatched == [fixtures.ALPHA.mystery_block] * truth.unmapped_count

    def test_a_rotated_and_scaled_pdf_instance_matches_its_legend_entry(
        self, folder: Path, alpha_plan: Any
    ) -> None:
        """The Done-when case on its own: PDF only, 90 degrees and 1.6 times the size."""
        [legend] = legends.detect(
            table_of(fixtures.legend_sheet(fixtures.ALPHA), "pdf", folder, "legend"), A3
        )
        plan, _ = alpha_plan
        placed = truth_blocks(plan)
        pdf = symbols.clusters(table_of(plan, "pdf", folder, "plan"))
        # The gate valves' third placement: turned 90 degrees, enlarged 1.6 times.
        gates = [c for c in pdf if block_at(placed, c.centre) == "VLV-GATE"]
        big = max(gates, key=lambda c: c.box[2] - c.box[0] + c.box[3] - c.box[1])
        assert big.signature is not None

        found = symbols.best_match(big.signature, [r.symbol.signature for r in legend.rows])  # type: ignore[misc]

        assert found is not None and found.how == "shape"
        assert legend.rows[found.index].description == "GATE VALVE"
        assert big.signature.size_mm == pytest.approx(
            1.6 * legend.rows[found.index].symbol.signature.size_mm,  # type: ignore[union-attr]
            rel=0.02,
        )

    def test_the_same_symbol_from_another_consultant_is_not_matched(self, folder: Path) -> None:
        """Beta draws a pendent sprinkler differently: Alpha's mapping must not claim it."""
        alpha = legends.detect(
            table_of(fixtures.legend_sheet(fixtures.ALPHA), "dxf", folder, "a"), A3
        )[0]
        beta = legends.detect(
            table_of(fixtures.legend_sheet(fixtures.BETA), "dxf", folder, "b"), A3
        )[0]
        alpha_entries = [row.symbol.signature for row in alpha.rows]

        for row in beta.rows:
            assert row.symbol.signature is not None
            assert symbols.best_match(row.symbol.signature, alpha_entries) is None  # type: ignore[arg-type]


def hashes(table: Any) -> dict[str | None, str | None]:
    return {c.block: c.block_hash for c in symbols.clusters(table)}


class TestBlockHashes:
    def test_a_block_hashes_the_same_in_every_drawing_and_under_another_name(self) -> None:
        first = geometry.from_parquet(
            extract_dxf(synthetic.dxf_bytes(fixtures.legend_sheet(fixtures.ALPHA)), None)["parquet"]
        )
        second = geometry.from_parquet(
            extract_dxf(synthetic.dxf_bytes(fixtures.plan_sheet(fixtures.ALPHA)[0]), None)[
                "parquet"
            ]
        )
        assert hashes(first)["SPK-PEND"] == hashes(second)["SPK-PEND"]
        # Both consultants' mystery symbols are the same star under different names.
        beta = geometry.from_parquet(
            extract_dxf(synthetic.dxf_bytes(fixtures.plan_sheet(fixtures.BETA)[0]), None)["parquet"]
        )
        assert hashes(second)["UNK-01"] == hashes(beta)["X-99"]
        assert hashes(second)["SPK-PEND"] != hashes(beta)["P-SPR"]


class TestTheDescriptor:
    @settings(max_examples=40, deadline=None)
    @given(
        angle=st.floats(0, 360),
        scale=st.floats(0.5, 3.0),
        dx=st.floats(-100, 100),
        dy=st.floats(-100, 100),
    )
    def test_it_ignores_position_rotation_and_scale(
        self, angle: float, scale: float, dx: float, dy: float
    ) -> None:
        reference = _bow_tie(0.0, 1.0, 0.0, 0.0)
        moved = _bow_tie(angle, scale, dx, dy)

        assert symbols.distance(reference, moved) < symbols.DEFAULT_TOLERANCE / 2

    def test_it_tells_similar_valves_apart(self) -> None:
        assert symbols.distance(_bow_tie(0, 1, 0, 0), _arrow_bar()) > 2 * symbols.DEFAULT_TOLERANCE


def _descriptor(builder: Builder) -> tuple[float, ...]:
    table = builder.table()
    described = symbols.describe(*symbols.sample(table, list(range(table.num_rows))))
    assert described is not None
    return described[0]


def _transform(
    points: list[tuple[float, float]], angle: float, scale: float, dx: float, dy: float
) -> list[float]:
    turn = math.radians(angle)
    flat: list[float] = []
    for x, y in points:
        flat += [
            50 + dx + scale * (x * math.cos(turn) - y * math.sin(turn)),
            50 + dy + scale * (x * math.sin(turn) + y * math.cos(turn)),
        ]
    return flat


def _bow_tie(angle: float, scale: float, dx: float, dy: float) -> tuple[float, ...]:
    builder = Builder(Method.PDF_VECTOR)
    for side in (-1, 1):
        points = [(side * 2.5, -1.25), (side * 2.5, 1.25), (0.0, 0.0)]
        builder.polyline(_transform(points, angle, scale, dx, dy), 1, closed=True)
    return _descriptor(builder)


def _arrow_bar() -> tuple[float, ...]:
    builder = Builder(Method.PDF_VECTOR)
    builder.polyline(
        _transform([(-2.5, -1.25), (-2.5, 1.25), (2.5, 0.0)], 0, 1, 0, 0), 1, closed=True
    )
    x0, y0, x1, y1 = _transform([(2.5, -1.25), (2.5, 1.25)], 0, 1, 0, 0)
    builder.line(x0, y0, x1, y1, 1)
    return _descriptor(builder)


class TestLegends:
    @pytest.mark.parametrize("form", ["dxf", "pdf"])
    def test_a_legend_sheet_gives_one_row_per_symbol_in_order(
        self, form: str, folder: Path
    ) -> None:
        table = table_of(fixtures.legend_sheet(fixtures.ALPHA), form, folder, "legend")

        [legend] = legends.detect(table, A3)

        assert legend.heading == "LEGEND"
        assert [row.description for row in legend.rows] == [
            symbol.description for symbol in fixtures.ALPHA.symbols
        ]

    @pytest.mark.parametrize("form", ["dxf", "pdf"])
    def test_category_columns_with_no_legend_heading_are_legends(
        self, form: str, folder: Path
    ) -> None:
        """Seen on a real tender set: the legend is set out in category columns side by side
        ("FIRE SPRINKLER SYSTEM", "VALVES & ACCESSORIES") and nothing says LEGEND outside the
        title block. A stamp above the columns and a stack of codes beside symbols are not
        legends."""
        table = table_of(fixtures.category_legend_sheet(), form, folder, "category-legend")

        found = legends.detect(table, A3)

        described = {symbol.block: symbol.description for symbol in fixtures.GAMMA.symbols}
        assert [(legend.heading, [row.description for row in legend.rows]) for legend in found] == [
            (heading, [described[block] for block in blocks])
            for heading, blocks in fixtures.GAMMA_COLUMNS
        ]
        assert all(row.symbol.signature is not None for legend in found for row in legend.rows)

    def test_a_category_legend_s_symbols_match_their_rows(self, folder: Path) -> None:
        table = table_of(fixtures.category_legend_sheet(), "dxf", folder, "category-legend")
        rows = [row for legend in legends.detect(table, A3) for row in legend.rows]

        signatures = [row.symbol.signature for row in rows if row.symbol.signature is not None]
        assert len(signatures) == len(rows), "every legend row's symbol is signed"
        for index, (row, signature) in enumerate(zip(rows, signatures, strict=True)):
            match = symbols.best_match(signature, signatures)
            assert match is not None and match.index == index, row.description

    @pytest.mark.parametrize(
        ("box", "line"),
        [
            ((0.0, 0.0, 16.7, 0.0), True),  # pipework: a short run of line, no height
            ((0.0, 0.0, 16.7, 2.1), True),  # fire-rated pipework: a long thin band
            ((0.0, 0.0, 0.1, 1.2), True),  # a sliver of a symbol, not the symbol
            ((0.0, 0.0, 2.8, 2.8), False),  # a sprinkler head
            ((0.0, 0.0, 6.0, 3.0), False),  # a valve
        ],
    )
    def test_a_line_sample_is_no_point_symbol(
        self, box: tuple[float, float, float, float], line: bool
    ) -> None:
        """Seen on a real tender: pipework rows in the legend show a sample of line. Matched
        as a symbol, every stroke of pipe on a plan became an object (6,389 on one sheet)."""
        assert legends.line_sample(symbols.Cluster(rows=(0,), box=box)) is line

    def test_the_title_block_is_not_a_legend_though_it_says_so(self, folder: Path) -> None:
        """The legend sheet's title is "LEGEND AND SYMBOLS", in its title block."""
        table = table_of(fixtures.legend_sheet(fixtures.ALPHA), "dxf", folder, "legend")

        found = legends.detect(table, A3)

        assert len(found) == 1
        assert found[0].box[0] < 100, "the legend on the left, not the title block on the right"

    @pytest.mark.parametrize("form", ["dxf", "pdf"])
    def test_a_legend_on_a_plan_encloses_its_own_examples(self, form: str, folder: Path) -> None:
        plan, _ = fixtures.plan_sheet(fixtures.ALPHA, with_legend=True)
        table = table_of(plan, form, folder, "plan-with-legend")

        [legend] = legends.detect(table, A3)
        inside = symbols.clusters(table, within=legend.box)
        outside = symbols.clusters(table, excluding=[legend.box])

        assert len(inside) == len(fixtures.ALPHA.symbols)
        placed = len(fixtures.ALPHA.symbols) * len(fixtures.PLACEMENTS) + 3
        assert len(outside) == placed


def test_descriptors_are_normalised_histograms() -> None:
    descriptor = _bow_tie(0, 1, 0, 0)
    parts = np.split(
        np.asarray(descriptor),
        np.cumsum([len(symbols.D2_BINS) - 1, len(symbols.RADIAL_BINS) - 1]),
    )
    assert [round(float(part.sum()), 6) for part in parts] == [1.0, 1.0, 1.0]


@settings(max_examples=40, deadline=None)
@given(st.integers(min_value=0, max_value=10_000))
def test_matching_against_stacked_candidates_is_matching_pair_by_pair(seed: int) -> None:
    """P1-11 compares a symbol with every candidate at once: the same answer as `distance`,
    one pair at a time, including candidates with no descriptor or an older width."""
    rng = np.random.default_rng(seed)

    def descriptor(width: int = symbols.WIDTH) -> tuple[float, ...]:
        return tuple(float(v) for v in rng.dirichlet(np.ones(width)) * 3)

    candidates = [
        symbols.Signature(descriptor(), 1.0, tolerance=float(rng.uniform(0.05, 0.9)))
        for _ in range(12)
    ]
    candidates[3] = symbols.Signature((), 1.0)
    candidates[7] = symbols.Signature(descriptor(symbols.WIDTH - 2), 1.0, tolerance=1.0)
    probe = symbols.Signature(descriptor(), 1.0, tolerance=float(rng.uniform(0.05, 0.9)))

    pairwise = [
        (symbols.distance(probe.descriptor, c.descriptor), min(probe.tolerance, c.tolerance))
        for c in candidates
    ]
    within = [
        (gap, i)
        for i, (gap, tolerance) in enumerate(pairwise)
        if candidates[i].descriptor and gap <= tolerance
    ]
    found = symbols.best_match(probe, symbols.Candidates(candidates))

    if not within:
        assert found is None
    else:
        gap, index = min(within)
        assert found is not None and found.index == index
        assert found.distance == pytest.approx(gap)
    assert symbols.best_match(probe, candidates) == found


def test_a_block_hash_matches_before_any_shape() -> None:
    shapes = symbols.Candidates()
    shapes.append(symbols.Signature(_bow_tie(0, 1, 0, 0), 1.0, block_hash="abc"))
    shapes.append(symbols.Signature(_bow_tie(0, 1, 0, 0), 1.0, block_hash="xyz"))

    found = symbols.best_match(symbols.Signature(_arrow_bar(), 1.0, block_hash="xyz"), shapes)

    assert found == symbols.Match(1, 0.0, "block_hash")


@pytest.mark.req("NFR-01")
def test_candidates_are_the_ones_a_value_a_cell_found() -> None:
    """The symbols found are the same whether the columns are read as arrays (as they now
    are, for a plan of two million primitives) or as a Python value a cell (as they were)."""
    import math

    from firebid.drawings.geometry import Builder, Kind, Method

    builder = Builder(Method.CAD)
    rng = np.random.default_rng(11)
    for index in range(400):
        x, y = float(rng.uniform(0, 400)), float(rng.uniform(0, 280))
        group = builder.group()
        size = float(rng.choice([0.2, 2.0, 4.0, 60.0]))
        layer = str(rng.choice(["FP-HEAD", "A-WALL"]))
        if index % 6 == 0:
            builder.circle(x, y, size / 2, group, layer=layer)
        else:
            builder.line(x, y, x + size, y + size / 2, group, layer=layer)
            if index % 4 == 0:
                builder.line(x + size, y + size / 2, x, y + size, group, layer=layer)
        if index % 9 == 0:
            builder.insert(
                "HEAD", x, y, group, box=(x - 1, y - 1, x + size + 1, y + size + 1), rotation=0.0
            )
    builder.text("NOTE", (10.0, 10.0, 30.0, 13.0), builder.group(), height=3.0)
    table = builder.table()

    def by_value(table: Any) -> list[symbols.Cluster]:
        columns = table.select(
            [
                "kind",
                "group",
                "block",
                "text",
                "value",
                "rotation",
                "layer",
                "color",
                "minx",
                "miny",
                "maxx",
                "maxy",
            ]
        ).to_pydict()
        kinds = columns["kind"]
        shape = {str(kind) for kind in symbols.SHAPE_KINDS}
        found: list[symbols.Cluster] = []
        in_insert: set[int] = set()
        by_group: dict[int, list[int]] = {}
        for row, kind in enumerate(kinds):
            if kind in shape:
                by_group.setdefault(columns["group"][row], []).append(row)
        for row, kind in enumerate(kinds):
            if kind != str(Kind.INSERT):
                continue
            parts = tuple(by_group.get(columns["group"][row], []))
            if not parts:
                continue
            in_insert.update(parts)
            box = symbols._box_of(columns, parts)
            if symbols._symbol_sized(box):
                found.append(
                    symbols.Cluster(
                        rows=parts,
                        box=box,
                        block=columns["block"][row],
                        block_hash=columns["text"][row],
                        rotation=columns["rotation"][row],
                        scale=columns["value"][row],
                    )
                )
        loose = [
            row
            for row, kind in enumerate(kinds)
            if kind in shape
            and row not in in_insert
            and columns["minx"][row] is not None
            and symbols._symbol_sized(symbols._box_of(columns, (row,)))
        ]
        for group in symbols._touching(columns, loose):
            box = symbols._box_of(columns, group)
            if symbols._symbol_sized(box):
                found.append(symbols.Cluster(rows=tuple(group), box=box))
        return found

    expected = by_value(table)
    found = symbols.candidates(table)

    assert len(expected) > 50 and any(cluster.block for cluster in expected)
    assert [(c.rows, c.block, c.block_hash, c.rotation, c.scale) for c in found] == [
        (c.rows, c.block, c.block_hash, c.rotation, c.scale) for c in expected
    ]
    for got, want in zip(found, expected, strict=True):
        assert all(math.isclose(a, b) for a, b in zip(got.box, want.box, strict=True))
