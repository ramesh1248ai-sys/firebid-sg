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
            # One line by itself is a straight stroke, which is no candidate.
            alone = len(group) == 1 and kinds[group[0]] == str(Kind.LINE)
            if symbols._symbol_sized(box) and not alone:
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


GREY, RED, BLACK = 0xD6D6D6, 0xFF0000, 0x000000


def _drawn(method: Method, shapes: list[tuple[str, float, int]]) -> list[tuple[float, float]]:
    """Where the candidates are on a sheet of these shapes: a kind, where along it, a pen."""
    builder = Builder(method)
    for kind, x, colour in shapes:
        group = builder.group()
        if kind == "stroke":  # one diagonal line: its box is as square as a symbol's
            builder.line(x, 50.0, x + 4.0, 54.0, group, color=colour)
        elif kind == "dashes":  # two strokes end to end along one line
            builder.line(x, 50.0, x + 2.0, 51.0, group, color=colour)
            builder.line(x + 2.2, 51.1, x + 4.2, 52.1, group, color=colour)
        elif kind == "vee":
            builder.line(x, 50.0, x + 2.0, 54.0, group, color=colour)
            builder.line(x + 2.0, 54.0, x + 4.0, 50.0, group, color=colour)
        elif kind == "circle":
            builder.circle(x + 2.0, 52.0, 2.0, group, color=colour)
    return [cluster.centre for cluster in symbols.candidates(builder.table())]


class TestWhatACandidateIs:
    def test_a_straight_stroke_is_no_candidate(self) -> None:
        found = _drawn(
            Method.PDF_VECTOR,
            [("stroke", 20.0, RED), ("dashes", 60.0, RED), ("vee", 100.0, RED)],
        )

        assert [round(x) for x, _ in found] == [102]

    def test_a_circle_is_a_candidate_though_it_has_no_ends(self) -> None:
        assert len(_drawn(Method.PDF_VECTOR, [("circle", 20.0, BLACK)])) == 1

    def test_the_screened_base_plan_of_a_pdf_is_no_candidate(self) -> None:
        found = _drawn(
            Method.PDF_VECTOR,
            [
                ("vee", 20.0, GREY),
                ("vee", 60.0, RED),
                ("vee", 100.0, BLACK),
                ("circle", 140.0, GREY),
            ],
        )

        assert [round(x) for x, _ in found] == [62, 102]

    def test_a_cad_file_is_not_printed_so_its_grey_is_a_layer_colour(self) -> None:
        assert len(_drawn(Method.CAD, [("vee", 20.0, GREY)])) == 1

    @pytest.mark.parametrize(
        ("colour", "is_screened"),
        [
            (0xD6D6D6, True),
            (0xBBBBBB, True),
            (0xEAEAEA, True),
            (0x000000, False),  # the services' annotation
            (0x404040, False),  # a dark grey pen
            (0xFFFFFF, False),
            (0xFF0000, False),
            (0xFFBBBB, False),  # a tint is a colour
            (-1, False),  # the source gave none
        ],
    )
    def test_a_screened_colour_is_a_light_grey(self, colour: int, is_screened: bool) -> None:
        assert symbols.screened(colour) is is_screened


def _head(builder: Builder, x: float, y: float, group: int, *, wide: float = 0.84) -> None:
    """An arrowhead pointing left, its tip at (x, y), 3.2 mm long."""
    builder.polyline(
        [x, y, x + 3.2, y + wide / 2, x + 3.2, y - wide / 2, x, y], group, closed=True, color=BLACK
    )


class TestALeaderIsNoCandidate:
    def found(self, builder: Builder) -> int:
        return len(symbols.candidates(builder.table()))

    def test_an_arrowhead_and_the_strokes_from_its_tip_to_a_note(self) -> None:
        builder = Builder(Method.PDF_VECTOR)
        group = builder.group()
        _head(builder, 20.0, 50.0, group)
        builder.line(20.0, 50.0, 28.0, 50.0, group, color=BLACK)
        builder.line(28.0, 50.0, 28.0, 44.0, group, color=BLACK)

        assert self.found(builder) == 0

    def test_an_arrowhead_alone_and_one_drawn_twice_as_outline_and_fill(self) -> None:
        builder = Builder(Method.PDF_VECTOR)
        _head(builder, 20.0, 50.0, builder.group())
        group = builder.group()
        _head(builder, 60.0, 50.0, group)
        _head(builder, 60.0, 50.0, group)

        assert self.found(builder) == 0

    def test_a_triangle_that_is_not_slender_is_a_symbol(self) -> None:
        """A check valve is a triangle and a bar: its triangle is as wide as it is long."""
        builder = Builder(Method.PDF_VECTOR)
        group = builder.group()
        _head(builder, 20.0, 50.0, group, wide=3.2)
        builder.line(20.0, 48.0, 20.0, 52.0, group, color=BLACK)

        assert self.found(builder) == 1

    def test_an_arrowhead_with_anything_else_is_a_symbol(self) -> None:
        builder = Builder(Method.PDF_VECTOR)
        group = builder.group()
        _head(builder, 20.0, 50.0, group)
        builder.circle(19.0, 50.0, 1.0, group, color=BLACK)

        assert self.found(builder) == 1

    def test_strokes_that_do_not_start_at_the_tip_are_not_its_leader(self) -> None:
        builder = Builder(Method.PDF_VECTOR)
        group = builder.group()
        _head(builder, 20.0, 50.0, group)
        builder.line(23.2, 49.0, 23.2, 53.0, group, color=BLACK)  # a bar across its back

        assert self.found(builder) == 1


class TestTheBoxBehindALetter:
    """A detector is a circle with an S in it; the S may be set on a filled box."""

    def detector(self, *, backed: bool, lettered: bool = True, box: float = 1.0) -> Any:
        builder = Builder(Method.PDF_VECTOR)
        group = builder.group()
        circle = geometry.arc_points(50.0, 50.0, 2.5, 0.0, 360.0, 18)
        builder.polyline(circle, group, closed=True, color=RED)
        if backed:
            half_w, half_h = 0.9 * box, 2.0 * box
            builder.hatch(
                [
                    50 - half_w, 50 - half_h, 50 + half_w, 50 - half_h,
                    50 + half_w, 50 + half_h, 50 - half_w, 50 + half_h,
                    50 - half_w, 50 - half_h,
                ],
                group,
                color=RED,
            )  # fmt: skip
        if lettered:
            builder.text("S", (49.2, 48.7, 50.8, 51.3), builder.group(), height=2.6)
        return builder.table()

    def signature(self, table: Any) -> symbols.Signature:
        (found,) = symbols.clusters(table)
        assert found.signature is not None
        return found.signature

    def test_a_symbol_is_the_same_with_and_without_the_box_behind_its_letter(self) -> None:
        plain = self.signature(self.detector(backed=False))
        backed = self.signature(self.detector(backed=True))

        assert symbols.distance(plain.descriptor, backed.descriptor) == 0.0

    def test_a_filled_box_with_no_letter_in_it_is_part_of_the_shape(self) -> None:
        plain = self.signature(self.detector(backed=False))
        boxed = self.signature(self.detector(backed=True, lettered=False))

        assert symbols.distance(plain.descriptor, boxed.descriptor) > symbols.DEFAULT_TOLERANCE

    def test_a_filled_box_much_larger_than_the_letter_is_part_of_the_shape(self) -> None:
        plain = self.signature(self.detector(backed=False))
        boxed = self.signature(self.detector(backed=True, box=1.7))

        assert symbols.distance(plain.descriptor, boxed.descriptor) > symbols.DEFAULT_TOLERANCE

    def test_the_box_is_left_out_when_the_letter_is_turned_with_a_skewed_wing(self) -> None:
        builder = Builder(Method.PDF_VECTOR)
        group = builder.group()
        builder.polyline(
            geometry.arc_points(50.0, 50.0, 2.5, 0.0, 360.0, 18), group, closed=True, color=RED
        )
        turn = math.radians(30.0)
        corners = [(-0.9, -2.0), (0.9, -2.0), (0.9, 2.0), (-0.9, 2.0), (-0.9, -2.0)]
        turned: list[float] = []
        for x, y in corners:
            turned += [
                50 + x * math.cos(turn) - y * math.sin(turn),
                50 + x * math.sin(turn) + y * math.cos(turn),
            ]
        builder.hatch(turned, group, color=RED)
        builder.text("S", (48.55, 48.45, 51.45, 51.55), builder.group(), height=2.6)

        backed = self.signature(builder.table())
        plain = self.signature(self.detector(backed=False))

        assert symbols.distance(plain.descriptor, backed.descriptor) == 0.0


class TestTheLettersInASymbol:
    """A smoke detector and a heat detector are the same circle: the S and the H tell them
    apart, so the letters written in a symbol are part of what it is."""

    def circle(self, letters: str | None, *, note: str | None = None) -> symbols.Signature:
        builder = Builder(Method.PDF_VECTOR)
        builder.polyline(
            geometry.arc_points(50.0, 50.0, 2.5, 0.0, 360.0, 18),
            builder.group(),
            closed=True,
            color=RED,
        )
        if letters:
            builder.text(letters, (49.2, 48.7, 50.8, 51.3), builder.group(), height=2.6)
        if note:  # written across the symbol, and far wider than it
            builder.text(note, (46.0, 49.0, 70.0, 51.0), builder.group(), height=2.0)
        (found,) = symbols.clusters(builder.table())
        assert found.signature is not None
        return found.signature

    def test_a_signature_carries_what_is_written_in_the_symbol(self) -> None:
        assert self.circle("S").letters == "S"
        assert self.circle(" s ").letters == "S"
        assert self.circle(None).letters == ""

    def test_a_note_that_runs_across_the_symbol_is_not_its_letters(self) -> None:
        assert self.circle(None, note="150 DIA SLEEVE").letters == ""
        assert self.circle("S", note="150 DIA SLEEVE").letters == "S"

    def test_letters_are_the_same_written_as_one_text_or_several_or_twice(self) -> None:
        def box(*words: tuple[str, float]) -> str:
            builder = Builder(Method.PDF_VECTOR)
            builder.polyline(
                [40.0, 48.0, 50.0, 48.0, 50.0, 52.0, 40.0, 52.0],
                builder.group(),
                closed=True,
                color=RED,
            )
            for word, x in words:
                builder.text(
                    word, (x, 49.0, x + 1.5 * len(word), 51.0), builder.group(), height=2.0
                )
            (found,) = symbols.clusters(builder.table())
            assert found.signature is not None
            return str(found.signature.letters)

        assert box(("2SFH", 42.0)) == "2FHS"
        assert box(("S", 46.0), ("F", 43.0)) == box(("FS", 43.5)) == "FS"
        assert box(("FS", 43.5), ("FS", 43.6)) == "FS"
        assert box(("T/S", 43.0)) == "ST"

    def test_the_same_shape_with_other_letters_is_another_symbol(self) -> None:
        rows = symbols.Candidates([self.circle("H"), self.circle("S"), self.circle(None)])

        assert symbols.best_match(self.circle("S"), rows) == symbols.Match(1, 0.0, "shape")
        assert symbols.best_match(self.circle(None), rows) == symbols.Match(2, 0.0, "shape")
        assert symbols.best_match(self.circle("T"), rows) is None
        assert symbols.near_match(self.circle("T"), rows, 3.0) is None

    def test_a_signature_stored_before_letters_were_read_matches_by_shape_alone(self) -> None:
        stored = self.circle("S").as_json()
        del stored["letters"]
        before = symbols.Signature.from_json(stored)

        assert before.letters is None
        assert symbols.best_match(self.circle("H"), [before]) is not None
        assert symbols.best_match(before, [self.circle("H")]) is not None

    def test_the_letters_are_kept_when_a_signature_is_stored(self) -> None:
        signature = self.circle("FS")

        assert symbols.Signature.from_json(signature.as_json()).letters == "FS"

    def test_candidates_added_one_by_one_keep_their_letters(self) -> None:
        rows = symbols.Candidates()
        for index in range(40):  # past the first block of rows, so the arrays have grown
            rows.append(self.circle(f"L{index}"))

        assert symbols.best_match(self.circle("L37"), rows) == symbols.Match(37, 0.0, "shape")


class TestLettersOfTwoOrMore:
    """A consultant draws a lettered box to fit where it stands: the letters say what it is
    more than its proportions do."""

    def box(self, letters: str | None, wide: float, high: float) -> symbols.Signature:
        builder = Builder(Method.PDF_VECTOR)
        builder.polyline(
            [40.0, 40.0, 40.0 + wide, 40.0, 40.0 + wide, 40.0 + high, 40.0, 40.0 + high],
            builder.group(),
            closed=True,
            color=RED,
        )
        if letters:
            middle = 40.0 + wide / 2, 40.0 + high / 2
            builder.text(
                letters,
                (middle[0] - 1.0, middle[1] - 0.8, middle[0] + 1.0, middle[1] + 0.8),
                builder.group(),
                height=1.6,
            )
        (found,) = symbols.clusters(builder.table())
        assert found.signature is not None
        return found.signature

    def test_the_same_letters_in_a_box_of_other_proportions_is_the_same_symbol(self) -> None:
        legend, plan = self.box("FI", 11.0, 5.4), self.box("FI", 3.5, 6.0)
        apart = symbols.distance(legend.descriptor, plan.descriptor)
        assert symbols.DEFAULT_TOLERANCE < apart <= symbols.LETTERED_TOLERANCE

        found = symbols.best_match(plan, [legend])

        assert found is not None and found.how == "letters"
        assert found.distance == pytest.approx(apart)

    def test_a_shape_within_the_ordinary_tolerance_is_matched_by_its_shape(self) -> None:
        found = symbols.best_match(self.box("FI", 11.0, 5.4), [self.box("FI", 11.0, 5.4)])

        assert found == symbols.Match(0, 0.0, "shape")

    def test_no_letters_and_other_letters_are_held_to_the_ordinary_tolerance(self) -> None:
        assert symbols.best_match(self.box(None, 3.5, 6.0), [self.box(None, 11.0, 5.4)]) is None
        assert symbols.best_match(self.box("FI", 3.5, 6.0), [self.box("FS", 11.0, 5.4)]) is None
        assert symbols.best_match(self.box("FI", 3.5, 6.0), [self.box(None, 11.0, 5.4)]) is None

    def test_one_letter_is_not_enough(self) -> None:
        assert symbols.best_match(self.box("S", 3.5, 6.0), [self.box("S", 11.0, 5.4)]) is None

    def test_the_nearest_of_two_rows_with_the_same_letters_is_taken(self) -> None:
        rows = [self.box("FI", 11.0, 5.4), self.box("FI", 3.6, 6.0)]

        found = symbols.best_match(self.box("FI", 3.5, 6.0), rows)

        assert found is not None and (found.index, found.how) == (1, "shape")


def test_what_was_set_aside_is_counted_by_rule() -> None:
    """A tender that prints its services in grey would lose them to the base-plan rule: the
    count is how anyone would know."""
    builder = Builder(Method.PDF_VECTOR)
    builder.polyline(
        geometry.arc_points(20.0, 20.0, 2.5, 0.0, 360.0, 18),
        builder.group(),
        closed=True,
        color=RED,
    )
    for x in (40.0, 50.0):  # two shapes of the screened base plan
        builder.polyline(
            geometry.arc_points(x, 20.0, 2.0, 0.0, 360.0, 18),
            builder.group(),
            closed=True,
            color=0xCCCCCC,
        )
    for y in (40.0, 45.0, 50.0):  # three straight strokes
        builder.line(60.0, y, 66.0, y, builder.group(), color=RED)
    _head(builder, 80.0, 20.0, builder.group())  # a leader: an arrowhead alone

    set_aside: dict[str, int] = {"straight": 10}
    found = symbols.candidates(builder.table(), set_aside)

    assert len(found) == 1
    assert set_aside == {"base_plan": 2, "straight": 13, "leader": 1}, "added to what it had"
    assert symbols.candidates(builder.table()) == found
