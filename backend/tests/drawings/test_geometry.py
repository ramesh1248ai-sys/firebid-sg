"""Sheet geometry from DXF and vector PDF, in one model (FR-VIS-01).

The synthetic plan's pipe runs are known exactly: branches of (columns - 1) x 3 m and a main
of (rows - 1) x 3 m, drawn at 1:100 on A3. Both formats must put them on the sheet at the
same length and in the same place.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pytest

from firebid.drawings import geometry
from firebid.drawings.geometry import Builder, Kind, Method, segments
from firebid.evals import synthetic
from firebid.parsing import geometry_pdf
from firebid.parsing.geometry_dxf import extract as extract_dxf

pytestmark = pytest.mark.req("FR-VIS-01")

COLUMNS, ROWS = 8, 6
BRANCH_SHEET_MM = (COLUMNS - 1) * synthetic.SPACING_MM / 100
MAIN_SHEET_MM = (ROWS - 1) * synthetic.SPACING_MM / 100


@pytest.fixture(scope="module")
def plan() -> object:
    document, _ = synthetic.general_arrangement(columns=COLUMNS, rows=ROWS)
    return document


@pytest.fixture(scope="module")
def from_dxf(plan: object) -> dict[str, object]:
    return extract_dxf(synthetic.dxf_bytes(plan), None)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def from_pdf(plan: object, tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    path = synthetic.write_pdf(plan, tmp_path_factory.mktemp("pdf") / "ga.pdf", live_text=True)  # type: ignore[arg-type]
    return geometry_pdf.extract(path.read_bytes(), 0)


def table_of(result: dict[str, object]):  # type: ignore[no-untyped-def]
    return geometry.from_parquet(result["parquet"])  # type: ignore[arg-type]


def long_lengths(result: dict[str, object]) -> list[float]:
    lengths = segments(table_of(result)).lengths
    return sorted(np.round(lengths[lengths > 50], 2).tolist())


class TestLengths:
    """Known lengths measured on the sheet (FR-VIS-05's 0.5% is checked at the scale level)."""

    @pytest.mark.parametrize("source", ["from_dxf", "from_pdf"])
    def test_every_branch_and_the_main_measure_exactly(
        self, source: str, request: pytest.FixtureRequest
    ) -> None:
        lengths = long_lengths(request.getfixturevalue(source))

        assert lengths.count(round(BRANCH_SHEET_MM, 2)) == ROWS
        assert lengths.count(round(MAIN_SHEET_MM, 2)) == 1

    def test_both_formats_agree_on_every_long_run(
        self, from_dxf: dict[str, object], from_pdf: dict[str, object]
    ) -> None:
        """Cross-format consistency: the same drawing, the same geometry within tolerance."""
        assert long_lengths(from_dxf) == pytest.approx(long_lengths(from_pdf), abs=0.05)

    def test_both_formats_put_the_sprinklers_in_the_same_places(
        self, from_dxf: dict[str, object], from_pdf: dict[str, object]
    ) -> None:
        dxf = table_of(from_dxf)
        circles = dxf.filter(pc.equal(dxf.column("kind"), pa.scalar("circle")))
        dxf_centres = np.column_stack(
            [circles.column("cx").to_numpy(), circles.column("cy").to_numpy()]
        )

        # A PDF circle is four Béziers, so it arrives as a small closed polyline.
        pdf = table_of(from_pdf)
        small = pdf.filter(
            pc.and_(
                pc.equal(pdf.column("kind"), pa.scalar("polyline")),
                pc.less(pc.subtract(pdf.column("maxx"), pdf.column("minx")), pa.scalar(2.0)),
            )
        )
        pdf_centres = np.column_stack(
            [
                (small.column("minx").to_numpy() + small.column("maxx").to_numpy()) / 2,
                (small.column("miny").to_numpy() + small.column("maxy").to_numpy()) / 2,
            ]
        )

        assert len(dxf_centres) == COLUMNS * ROWS
        for centre in dxf_centres:
            nearest = np.min(np.hypot(*(pdf_centres - centre).T))
            assert nearest < 0.1, f"no PDF sprinkler within 0.1 mm of {centre}"


class TestWhatIsRecorded:
    def test_every_primitive_says_how_it_was_extracted(
        self, from_dxf: dict[str, object], from_pdf: dict[str, object]
    ) -> None:
        assert set(table_of(from_dxf).column("method").to_pylist()) == {str(Method.CAD)}
        assert set(table_of(from_pdf).column("method").to_pylist()) == {str(Method.PDF_VECTOR)}

    def test_a_dxf_keeps_layers_and_its_stated_scale(self, from_dxf: dict[str, object]) -> None:
        table = table_of(from_dxf)
        assert {synthetic.LAYER_PIPE, synthetic.LAYER_SPRINKLER} <= set(
            table.column("layer").to_pylist()
        )
        views = cast(list[dict[str, object]], from_dxf["views"])
        view = views[0]
        assert (view["denominator"], view["scale_source"]) == (100.0, "stated")
        assert from_dxf["page"] == [0.0, 0.0, 420.0, 297.0]

    def test_a_pdf_keeps_stroke_colour_and_text_positions(
        self, from_pdf: dict[str, object]
    ) -> None:
        table = table_of(from_pdf)
        spans = geometry.texts(table)
        number = next(span for span in spans if span["text"] == "FP-L05-201")
        assert number["minx"] > 280, "the drawing number sits in the bottom-right title block"
        assert number["miny"] > 270
        assert any(color >= 0 for color in table.column("color").to_pylist())

    def test_a_page_pdfium_cannot_read_falls_back_and_says_so(
        self, plan: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = synthetic.write_pdf(plan, tmp_path / "ga.pdf").read_bytes()  # type: ignore[arg-type]

        def broken(*_args: object) -> bytes:
            raise RuntimeError("PDFium could not walk this page")

        monkeypatch.setattr(geometry_pdf, "_pdfium", broken)
        result = geometry_pdf.extract(payload, 0)

        assert result["method"] == str(Method.PDF_FALLBACK)
        assert "PDFium could not walk" in str(result["primary_failure"])
        table = table_of(result)
        assert set(table.column("method").to_pylist()) == {str(Method.PDF_FALLBACK)}
        assert round(BRANCH_SHEET_MM, 2) in long_lengths(result)


class TestTheModel:
    def test_a_closed_polyline_includes_its_closing_segment(self) -> None:
        builder = Builder(Method.CAD)
        builder.polyline([0, 0, 10, 0, 10, 5], builder.group(), closed=True)
        builder.line(0, 0, 3, 4, builder.group())

        lengths = sorted(segments(builder.table()).lengths.round(4).tolist())

        assert lengths == [5.0, 5.0, 10.0, pytest.approx(11.1803, abs=1e-4)]

    def test_it_survives_a_round_trip_through_parquet(self) -> None:
        builder = Builder(Method.CAD)
        builder.circle(5, 5, 1, builder.group(), layer="FP")
        builder.dimension(0, 0, 10, 0, builder.group(), value=1000.0, text="1000")

        table = geometry.from_parquet(geometry.to_parquet(builder.table()))

        assert geometry.counts(table) == {str(Kind.CIRCLE): 1, str(Kind.DIMENSION): 1}
        assert table.column("value").to_pylist() == [None, 1000.0]

    def test_an_unknown_field_is_refused(self) -> None:
        builder = Builder(Method.CAD)
        with pytest.raises(TypeError, match="unknown primitive fields"):
            builder.line(0, 0, 1, 1, builder.group(), colour=3)
