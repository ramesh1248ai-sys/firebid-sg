"""Views, scales, grids: what may be measured, and where things are (FR-VIS-05/07/08).

The synthetic plan is drawn at 1:100 with its dimensions and a structural grid, so its scale
can be verified from the drawing itself. Each branch runs (columns - 1) x 3 m = 21 m. The
same drawing without dimensions states 1:100 with nothing to confirm it, and the schematic is
marked not to scale: both are refused.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from firebid.drawings import geometry, grids, scale, views
from firebid.drawings.scale import NotMeasurable, ScaleStatus
from firebid.drawings.views import ViewKind
from firebid.evals import synthetic
from firebid.parsing import geometry_pdf
from firebid.parsing.geometry_dxf import extract as extract_dxf

COLUMNS, ROWS = 8, 6
BRANCH_MM = (COLUMNS - 1) * synthetic.SPACING_MM
A3 = [0.0, 0.0, 420.0, 297.0]


def _from(document: Any, form: str, folder: Path) -> tuple[Any, tuple[float, ...], list[Any]]:
    if form == "dxf":
        result = extract_dxf(synthetic.dxf_bytes(document), None)
    else:
        path = synthetic.write_pdf(document, folder / "sheet.pdf", live_text=True)
        result = geometry_pdf.extract(path.read_bytes(), 0)
        result["page"] = A3
    return geometry.from_parquet(result["parquet"]), tuple(result["page"]), result.get("views", [])


def analysed(document: Any, form: str, folder: Path, sheet_scale: str) -> list[views.Analysed]:
    table, page, source_views = _from(document, form, folder)
    return views.analyse(table, page, sheet_scale, source_views)  # type: ignore[arg-type]


def branch_points(table: Any) -> list[list[tuple[float, float]]]:
    """Every branch's two ends on the sheet, as a person would click them."""
    lines = geometry.segments(table)
    lengths = lines.lengths
    # By length alone: a PDF has no layers, and nothing else on the sheet is 210 mm long.
    wanted = np.isclose(lengths, BRANCH_MM / 100, atol=0.05)
    return [
        [(float(lines.x0[i]), float(lines.y0[i])), (float(lines.x1[i]), float(lines.y1[i]))]
        for i in np.flatnonzero(wanted)
    ]


FORMS = ["dxf", "pdf"]


@pytest.mark.req("FR-VIS-05")
class TestMeasurement:
    @pytest.mark.parametrize("form", FORMS)
    def test_a_verified_plan_measures_known_lengths_within_half_a_percent(
        self, form: str, tmp_path: Path
    ) -> None:
        document, _ = synthetic.general_arrangement(columns=COLUMNS, rows=ROWS)
        table, page, source_views = _from(document, form, tmp_path)
        (plan,) = views.analyse(table, page, "1:100", source_views)  # type: ignore[arg-type]

        assert plan.verdict.status is ScaleStatus.VERIFIED
        assert plan.verdict.denominator == 100
        runs = branch_points(table)
        assert len(runs) == ROWS
        for run in runs:
            assert scale.measure(run, plan.verdict) == pytest.approx(BRANCH_MM, rel=0.005)

    @pytest.mark.parametrize("form", FORMS)
    def test_a_stated_scale_nothing_confirms_is_refused(self, form: str, tmp_path: Path) -> None:
        document, _ = synthetic.general_arrangement(
            with_grid=False, with_dimensions=False, view_title=None
        )
        table, page, source_views = _from(document, form, tmp_path)
        (plan,) = views.analyse(table, page, "1:100", source_views)  # type: ignore[arg-type]

        assert plan.verdict.status is ScaleStatus.UNVERIFIED
        with pytest.raises(NotMeasurable, match="unverified"):
            scale.measure(branch_points(table)[0], plan.verdict)

    @pytest.mark.parametrize("form", FORMS)
    def test_a_view_marked_not_to_scale_is_refused(self, form: str, tmp_path: Path) -> None:
        document, _ = synthetic.not_to_scale_sheet()
        (view,) = analysed(document, form, tmp_path, "NTS")

        assert view.view.kind is ViewKind.SCHEMATIC
        assert view.verdict.status is ScaleStatus.NTS
        with pytest.raises(NotMeasurable, match="nts"):
            scale.measure([(0, 0), (10, 0)], view.verdict)

    def test_calibration_unlocks_measurement(self, tmp_path: Path) -> None:
        document, _ = synthetic.general_arrangement(
            with_grid=False, with_dimensions=False, view_title=None
        )
        table, page, source_views = _from(document, "pdf", tmp_path)
        (plan,) = views.analyse(table, page, "1:100", source_views)  # type: ignore[arg-type]
        first, *others = branch_points(table)

        # A person clicks the ends of one branch and says it is 21 m.
        verdict = scale.calibrated((first[0], first[1]), BRANCH_MM, plan.view.stated)

        assert verdict.status is ScaleStatus.CALIBRATED
        assert verdict.measurable
        assert scale.measure(others[0], verdict) == pytest.approx(BRANCH_MM, rel=0.005)

    def test_dimensions_that_disagree_with_the_stated_scale_conflict(self) -> None:
        stated = scale.parse_stated("SCALE 1:50")
        evidence = [scale.Evidence(3000, 30.0, "figure")]  # a 1:100 dimension

        verdict = scale.verify(stated, evidence)

        assert verdict.status is ScaleStatus.CONFLICTING
        assert not verdict.measurable

    @pytest.mark.parametrize(
        ("text", "denominator", "nts"),
        [
            ("1:100", 100, False),
            ("SCALE 1:50 @ A1", 50, False),
            ("NTS", None, True),
            ("SCHEMATIC - NOT TO SCALE", None, True),
            ("AS SHOWN", None, False),
        ],
    )
    def test_stated_scales_are_read(self, text: str, denominator: float | None, nts: bool) -> None:
        stated = scale.parse_stated(text)
        assert (stated.denominator, stated.nts) == (denominator, nts)


@pytest.mark.req("FR-VIS-07")
class TestWhereThingsAre:
    @pytest.mark.parametrize("form", FORMS)
    def test_a_point_has_a_grid_reference_and_level(self, form: str, tmp_path: Path) -> None:
        document, _ = synthetic.general_arrangement(columns=COLUMNS, rows=ROWS)
        (plan,) = analysed(document, form, tmp_path, "1:100")

        assert plan.grid is not None
        assert [line.label for line in plan.grid.across] == ["A", "B", "C", "D", "E"]
        assert [line.label for line in plan.grid.up] == ["1", "2", "3", "4"]
        b, two = plan.grid.across[1].position, plan.grid.up[1].position
        assert plan.grid.reference(b, two) == "Grid B2"
        assert plan.grid.reference(b + 15, two + 15) == f"Grid B1{grids.BAY}C2"
        assert plan.view.level == "L05"

    def test_the_grid_survives_storage(self, tmp_path: Path) -> None:
        document, _ = synthetic.general_arrangement()
        (plan,) = analysed(document, "dxf", tmp_path, "1:100")
        assert plan.grid is not None

        assert grids.GridSystem.from_json(plan.grid.as_json()) == plan.grid

    def test_a_level_is_read_from_a_view_title(self) -> None:
        assert views.level_of("LEVEL 5 FIRE SPRINKLER LAYOUT PLAN") == "L05"
        assert views.level_of("BASEMENT LEVEL B2 PLAN") == "B2"
        assert views.level_of("RISER SCHEMATIC") is None


@pytest.mark.req("FR-VIS-08")
class TestViews:
    @pytest.mark.parametrize(
        ("title", "kind"),
        [
            ("LEVEL 5 FIRE SPRINKLER LAYOUT PLAN", ViewKind.PLAN),
            ("ENLARGED PLAN - RISER AREA", ViewKind.ENLARGED_PLAN),
            ("KEY PLAN", ViewKind.KEY_PLAN),
            ("SECTION A-A", ViewKind.SECTION),
            ("SPRINKLER RISER SCHEMATIC", ViewKind.SCHEMATIC),
            ("TYPICAL DETAIL OF PENDENT HEAD", ViewKind.DETAIL),
        ],
    )
    def test_the_kind_comes_from_the_title(self, title: str, kind: ViewKind) -> None:
        assert views.kind_of(title) is kind

    @pytest.mark.parametrize("form", FORMS)
    def test_an_enlarged_plan_is_found_inside_the_general_plan_in_grid_space(
        self, form: str, tmp_path: Path
    ) -> None:
        """Different sheets, different scales, the same gridlines: that is the overlap."""
        (general,) = analysed(synthetic.general_arrangement()[0], form, tmp_path, "1:100")
        (enlarged,) = analysed(synthetic.enlarged_plan()[0], form, tmp_path, "1:50")

        assert general.view.kind is ViewKind.PLAN
        assert enlarged.view.kind is ViewKind.ENLARGED_PLAN
        assert enlarged.view.stated.denominator == 50
        assert general.grid is not None and enlarged.grid is not None
        general_box = general.grid.box(general.view.extent)
        enlarged_box = enlarged.grid.box(enlarged.view.extent)
        assert general_box is not None and enlarged_box is not None
        assert grids.overlap(general_box, enlarged_box) > 0.95

    def test_the_title_block_is_not_a_view(self, tmp_path: Path) -> None:
        (plan,) = analysed(synthetic.general_arrangement()[0], "dxf", tmp_path, "1:100")
        # The title block's cells start at x = 280 mm; the plan stops before them.
        assert plan.view.extent[2] < 300
