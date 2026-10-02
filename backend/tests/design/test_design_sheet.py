"""A design-intent sheet, from its DXF to a proposed layout (FR-DSN-01 to 03).

The synthetic sheet (`synthetic_design`) goes through the same extraction and view analysis
as an uploaded drawing: its scale is verified from its own dimensions, its criteria are read
from its notes, and its layout is checked against the building it draws.
"""

from __future__ import annotations

import itertools
import math

import pyarrow as pa
import pytest

from firebid.design import basis, layout, rooms
from firebid.design.basis import NoteLine
from firebid.drawings import geometry
from firebid.drawings.detection import ViewInfo, views_of
from firebid.evals import synthetic, synthetic_design
from firebid.parsing.geometry_dxf import extract


@pytest.fixture(scope="module")
def sheet() -> tuple[pa.Table, ViewInfo]:
    result = extract(synthetic.dxf_bytes(synthetic_design.design_intent_plan()), None)
    table = geometry.from_parquet(result["parquet"])
    page = (result["page"][0], result["page"][1], result["page"][2], result["page"][3])
    views = views_of(table, page, "1:100", result.get("views"))
    return table, next(view for view in views if view.kind == "plan")


def notes(table: pa.Table) -> list[NoteLine]:
    return [
        NoteLine(
            str(span["text"]),
            float(span["minx"]),
            float(span["miny"]),
            float(span["maxy"]) - float(span["miny"]),
        )
        for span in geometry.texts(table)
    ]


@pytest.mark.req("FR-DSN-01")
def test_the_sheet_says_the_design_is_the_contractor_s_and_states_its_criteria(
    sheet: tuple[pa.Table, ViewInfo],
) -> None:
    table, _ = sheet

    assert basis.design_intent(notes(table)) == synthetic_design.INTENT_NOTE
    concealed, exposed = basis.criteria_from_notes(notes(table), "FP-L10-01")
    assert (concealed.max_spacing_mm, concealed.max_area_m2) == ((4000, 3000), 12.0)
    assert (exposed.max_spacing_mm, exposed.max_area_m2) == ((3000, 3000), 9.0)
    assert concealed.title == "(ORDINARY HAZARD GROUP III)"


@pytest.mark.req("FR-DSN-01")
def test_a_sheet_of_drawn_heads_is_not_a_design_intent_sheet() -> None:
    document, _ = synthetic.general_arrangement()
    table = geometry.from_parquet(extract(synthetic.dxf_bytes(document), None)["parquet"])

    assert basis.design_intent(notes(table)) is None
    assert basis.criteria_from_notes(notes(table), "FP-L05-201") == []


@pytest.mark.req("FR-DSN-02")
def test_the_spaces_are_the_building_s(sheet: tuple[pa.Table, ViewInfo]) -> None:
    table, view = sheet
    assert view.denominator == 100.0  # verified from the sheet's own dimensions

    found = rooms.find(table, view.extent, view.denominator)

    by_name = {space.name: space for space in found.spaces}
    assert by_name["WARD A"].area_m2 == pytest.approx(80.0, rel=0.03)
    assert by_name["STORE"].area_m2 == pytest.approx(12.0, rel=0.05)
    assert len(found.shafts) == 1
    assert found.footprint_m2 == pytest.approx(320.0 - 9.0, rel=0.02)


@pytest.mark.req("FR-DSN-03")
def test_the_proposed_layout_meets_the_criterion_in_every_space(
    sheet: tuple[pa.Table, ViewInfo],
) -> None:
    table, view = sheet
    assert view.denominator is not None
    criterion = basis.criteria_from_notes(notes(table), "FP-L10-01")[0]
    rules = basis.load_rules()
    found = rooms.find(table, view.extent, view.denominator)
    seg = geometry.segments(table)
    layers = table.column("layer").to_pylist()
    mains = [
        [(float(seg.x0[i]), float(seg.y0[i])), (float(seg.x1[i]), float(seg.y1[i]))]
        for i in range(len(seg))
        if layers[int(seg.row[i])] == synthetic.LAYER_PIPE
    ]

    plan = layout.plan(
        found.spaces, view.denominator, criterion, rules, level=view.level, pipes=mains
    )

    for space in plan.spaces:
        assert space.heads >= math.ceil(space.area_m2 / criterion.max_area_m2 - 1e-6)
    by_name = {space.name: space for space in plan.spaces}
    # The ward is an open floor, on the 2.8 m grid: 4 x 3. The store is one head at 12 m2.
    assert by_name["WARD A"].heads == 12
    assert by_name["STORE"].heads == 1
    # The main runs through the open floor, within reach of every row.
    assert plan.totals()["remote_rows"] == 0
    assert all(pipe.feed_to is not None for pipe in plan.ranges)
    # Laying out again gives the same proposal.
    again = layout.plan(
        found.spaces, view.denominator, criterion, rules, level=view.level, pipes=mains
    )
    assert [(h.x, h.y) for h in again.heads] == [(h.x, h.y) for h in plan.heads]


@pytest.mark.req("FR-DSN-03")
def test_a_range_pipe_s_stretches_add_up_to_its_lengths() -> None:
    from tests.design.test_layout import GRID, OH, rectangle

    plan = layout.plan([rectangle(28.0, 2.8)], 100.0, OH, GRID, pipes=[[(0.0, 0.0), (0.0, 100.0)]])

    (pipe,) = plan.ranges
    stretches = pipe.stretches()
    assert [dn for dn, _, _ in stretches] == sorted({dn for dn, _ in pipe.pieces}, reverse=True)
    totals: dict[int, int] = {}
    for dn, length, points in stretches:
        totals[dn] = totals.get(dn, 0) + length
        assert len(points) >= 2
    assert totals == pipe.lengths_mm
    # Each stretch starts where the one before it ended.
    for (_, _, before), (_, _, after) in itertools.pairwise(stretches):
        assert before[-1] == after[0]
    record = basis.rule_record(OH, GRID)
    assert (record["rule_key"], record["rule_version"]) == ("sprinkler_layout", 1)
    assert {item["name"] for item in record["inputs"]} >= {"max_area_m2", "grid_along_mm"}
