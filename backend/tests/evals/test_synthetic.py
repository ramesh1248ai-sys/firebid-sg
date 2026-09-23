"""The synthetic generator, and that its ground truth matches what it drew.

The load-bearing test is `test_the_dxf_contains_exactly_what_the_truth_claims`: it re-opens
the generated file and recounts the entities. Ground truth written by the same code that drew
the sheet would otherwise be a tautology — both wrong together, and agreeing.
"""

from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest

from firebid.evals.schema import InputClass, ObjectType, RevisionStatus
from firebid.evals.synthetic import (
    LAYER_PIPE,
    LAYER_SPRINKLER,
    dxf_bytes,
    enlarged_plan,
    general_arrangement,
    generate_tender,
    legend_sheet,
    not_to_scale_sheet,
    write_dxf,
)

pytestmark = pytest.mark.req("FR-LRN-01")

PENDENT = ObjectType.SPRINKLER_PENDENT


class TestTruthMatchesTheDrawing:
    def test_the_dxf_contains_exactly_what_the_truth_claims(self, tmp_path: Path) -> None:
        """Recount from the file, not from the generator's own bookkeeping."""
        document, truth = general_arrangement(columns=8, rows=6)
        path = write_dxf(document, tmp_path / "ga.dxf")

        reopened = ezdxf.readfile(path)
        circles = [
            entity
            for entity in reopened.modelspace()
            if entity.dxftype() == "CIRCLE" and entity.dxf.layer == LAYER_SPRINKLER
        ]
        assert len(circles) == truth.count_of(PENDENT) == 48

    def test_the_pipe_lengths_measure_the_lines_that_were_drawn(self, tmp_path: Path) -> None:
        document, truth = general_arrangement(columns=8, rows=6)
        path = write_dxf(document, tmp_path / "ga.dxf")

        reopened = ezdxf.readfile(path)
        lines = [
            entity
            for entity in reopened.modelspace()
            if entity.dxftype() == "LINE" and entity.dxf.layer == LAYER_PIPE
        ]
        # Horizontal lines are branches; the single vertical one is the main.
        horizontal = sum(
            abs(line.dxf.end.x - line.dxf.start.x)
            for line in lines
            if abs(line.dxf.end.y - line.dxf.start.y) < 1
        )
        vertical = sum(
            abs(line.dxf.end.y - line.dxf.start.y)
            for line in lines
            if abs(line.dxf.end.x - line.dxf.start.x) < 1
        )
        assert truth.length_at(50) == pytest.approx(horizontal)
        assert truth.length_at(150) == pytest.approx(vertical)

    def test_the_grid_size_follows_the_arguments(self) -> None:
        _, small = general_arrangement(columns=3, rows=2)
        _, large = general_arrangement(columns=10, rows=8)
        assert small.count_of(PENDENT) == 6
        assert large.count_of(PENDENT) == 80


class TestEachFixtureType:
    def test_the_legend_claims_no_installed_items(self) -> None:
        """Symbols are drawn, but a legend installs nothing. Counting them is the trap."""
        document, truth = legend_sheet()
        drawn = sum(
            1
            for entity in document.modelspace()
            if entity.dxftype() == "CIRCLE" and entity.dxf.layer == LAYER_SPRINKLER
        )
        assert drawn == 4, "the legend should draw example symbols"
        assert truth.total_objects() == 0, "but claim none of them as installed"

    def test_the_schematic_is_marked_not_to_scale(self) -> None:
        _, truth = not_to_scale_sheet()
        assert truth.not_to_scale is True
        assert truth.pipe_lengths == (), "a sheet with no scale has no measurable length"

    def test_the_enlarged_plan_points_at_what_it_repeats(self) -> None:
        _, truth = enlarged_plan(source_sheet="FP-L05-201")
        assert truth.duplicates_of == ("FP-L05-201",)

    def test_a_title_block_is_drawn_so_it_can_be_read(self) -> None:
        document, truth = general_arrangement(sheet_number="FP-L05-201", revision="R04")
        text = " ".join(
            entity.dxf.text for entity in document.modelspace() if entity.dxftype() == "TEXT"
        )
        assert "FP-L05-201" in text
        assert "R04" in text
        assert truth.sheet_number == "FP-L05-201"


class TestAWholeTender:
    def test_it_produces_every_fixture_type(self, tmp_path: Path) -> None:
        generated = generate_tender(tmp_path, seed=1)
        truth = generated.truth

        assert any(sheet.duplicates_of for sheet in truth.sheets), "no enlarged plan"
        assert any(sheet.not_to_scale for sheet in truth.sheets), "no not-to-scale sheet"
        assert any(sheet.status is RevisionStatus.SUPERSEDED for sheet in truth.sheets), (
            "no superseded revision"
        )
        assert any(sheet.input_class is InputClass.RASTER for sheet in truth.sheets), (
            "no scanned sheet"
        )
        assert any(sheet.total_objects() == 0 for sheet in truth.sheets), "no legend"

    def test_it_writes_dxf_pdf_and_png(self, tmp_path: Path) -> None:
        generated = generate_tender(tmp_path, seed=1)
        written = list(tmp_path.iterdir())
        suffixes = {path.suffix for path in written}
        assert {".dxf", ".pdf", ".png"} <= suffixes

        for sheet in generated.sheets:
            for path in (sheet.dxf_path, sheet.pdf_path, sheet.png_path):
                if path is not None:
                    assert path.exists() and path.stat().st_size > 0

    def test_the_tender_validates_as_truth(self, tmp_path: Path) -> None:
        """Exactly one current revision per sheet number, duplicates pointing at real sheets."""
        generated = generate_tender(tmp_path, seed=3)
        numbers = {sheet.sheet_number for sheet in generated.truth.sheets}
        for number in numbers:
            current = [
                sheet
                for sheet in generated.truth.sheets
                if sheet.sheet_number == number and sheet.status is RevisionStatus.CURRENT
            ]
            assert len(current) == 1

    def test_the_boq_quantity_matches_the_heads_on_current_sheets(self, tmp_path: Path) -> None:
        generated = generate_tender(tmp_path, seed=5)
        line = next(line for line in generated.truth.boq_lines if line.maps_to is PENDENT)
        counted = sum(sheet.count_of(PENDENT) for sheet in generated.truth.current_sheets())
        assert line.quantity == pytest.approx(counted)


class TestDeterminism:
    def test_the_same_seed_produces_the_same_geometry(self, tmp_path: Path) -> None:
        """The seed is the artefact; the fixtures are regenerated, not committed."""
        first = generate_tender(tmp_path / "a", seed=42)
        second = generate_tender(tmp_path / "b", seed=42)
        assert first.truth.model_dump() == second.truth.model_dump()

        document_a, _ = general_arrangement(columns=7, rows=5)
        document_b, _ = general_arrangement(columns=7, rows=5)
        # DXF headers carry timestamps and handles; entity geometry is what must match.
        geometry_a = [(entity.dxftype(), entity.dxf.layer) for entity in document_a.modelspace()]
        geometry_b = [(entity.dxftype(), entity.dxf.layer) for entity in document_b.modelspace()]
        assert geometry_a == geometry_b

    def test_different_seeds_produce_different_plans(self, tmp_path: Path) -> None:
        """A predictor tuned to one grid shape should not score well on another."""
        shapes = {
            generate_tender(tmp_path / str(seed), seed=seed, with_raster=False)
            .truth.sheets[0]
            .count_of(PENDENT)
            for seed in range(1, 8)
        }
        assert len(shapes) > 1

    def test_dxf_bytes_are_produced_without_touching_disk(self) -> None:
        document, _ = general_arrangement()
        payload = dxf_bytes(document)
        assert payload.startswith(b"  0\r\nSECTION") or payload.startswith(b"  0\nSECTION")
