"""Detection on a synthetic installation: exact counts, lengths per DN, runs (FR-VIS-03).

The fixture is one floor of a wet-pipe system (see `synthetic_network`): a riser, a DN150
main through a gate valve and a non-return valve reducing to DN100, and six DN50 branches
of four heads each. Every count and every length per DN is known exactly, and must come out
the same from the DXF and from its PDF, where symbols and pipes are only line work.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pytest

from firebid.drawings import geometry
from firebid.drawings.detection import (
    TypeInfo,
    detect,
    missing_fields,
    placed_from_legend,
    views_of,
)
from firebid.drawings.pipe_network import Profile
from firebid.evals import synthetic
from firebid.evals.synthetic_network import DESCRIBED, network_plan
from firebid.parsing import geometry_pdf
from firebid.parsing.geometry_dxf import extract as extract_dxf
from firebid.services.object_library import seed_types

KINDS = {kind.key: kind for kind in seed_types()}
FORMS = ["dxf", "pdf"]


def type_of(description: str) -> TypeInfo | None:
    """Every legend row as a person would confirm it."""
    key = DESCRIBED.get(description)
    if key is None:
        return None
    kind = KINDS[key]
    extra = {"fitting": "reducer"} if key == "fitting" else {}
    return TypeInfo(kind.key, kind.category, kind.measure, extra)


def sheet(document: Any, form: str, folder: Path) -> tuple[Any, tuple[float, ...], Any]:
    if form == "dxf":
        result = extract_dxf(synthetic.dxf_bytes(document), None)
    else:
        path = synthetic.write_pdf(document, folder / "plan.pdf", live_text=True)
        result = {**geometry_pdf.extract(path.read_bytes(), 0), "page": [0.0, 0.0, 420.0, 297.0]}
    return geometry.from_parquet(result["parquet"]), tuple(result["page"]), result.get("views")


def detected(document: Any, form: str, folder: Path, **options: Any) -> Any:
    table, page, source_views = sheet(document, form, folder)
    views = views_of(table, page, "1:100", source_views)  # type: ignore[arg-type]
    placed, excluded = placed_from_legend(table, page, type_of)  # type: ignore[arg-type]
    return detect(table, placed, views, excluded=excluded, **options)


def lengths_by_dn(found: Any) -> dict[int | str, float]:
    totals: dict[int | str, float] = defaultdict(float)
    for run in found.runs:
        totals[run.dn or run.size_status] += run.length_mm or 0.0
    return dict(totals)


@pytest.fixture(scope="module")
def clean() -> Any:
    return network_plan()


@pytest.mark.req("FR-VIS-03")
class TestCountsAndLengths:
    @pytest.mark.parametrize("form", FORMS)
    def test_sprinkler_and_valve_counts_are_exact(
        self, form: str, clean: Any, tmp_path: Path
    ) -> None:
        document, truth = clean
        found = detected(document, form, tmp_path)

        counts = Counter(o.object_type for o in found.objects if o.kind == "object")

        assert dict(counts) == truth.counts
        assert Counter(o.kind for o in found.objects)["riser"] == 1

    @pytest.mark.parametrize("form", FORMS)
    def test_pipe_length_per_dn_is_within_half_a_percent(
        self, form: str, clean: Any, tmp_path: Path
    ) -> None:
        document, truth = clean
        found = detected(document, form, tmp_path)

        measured = lengths_by_dn(found)

        assert set(measured) == set(truth.lengths)
        for dn, expected in truth.lengths.items():
            assert measured[dn] == pytest.approx(expected, rel=0.005), dn

    @pytest.mark.parametrize("form", FORMS)
    def test_runs_are_mains_and_branches_and_every_head_has_a_drop(
        self, form: str, clean: Any, tmp_path: Path
    ) -> None:
        document, truth = clean
        found = detected(document, form, tmp_path)

        branches = [run for run in found.runs if run.run_class == "branch"]
        drops = [o for o in found.objects if o.kind == "drop"]
        heads = sum(v for k, v in truth.counts.items() if k.startswith("sprinkler_"))

        assert len(branches) == 6 and all(run.dn == 50 for run in branches)
        assert {run.dn for run in found.runs if run.run_class == "main"} == {150, 100}
        assert len(drops) == heads
        assert all(d.attributes["vertical_not_drawn"] for d in drops)
        assert {d.attributes["nominal_diameter_mm"] for d in drops} == {50}

    def test_a_line_of_the_pipe_colour_that_reaches_nothing_is_not_pipe(
        self, tmp_path: Path
    ) -> None:
        document, truth = network_plan()
        space = document.modelspace()
        space.add_line((21_000, 17_000), (24_000, 17_000), dxfattribs={"layer": "FP-PIPE"})

        found = detected(document, "dxf", tmp_path)

        assert lengths_by_dn(found) == pytest.approx(truth.lengths)

    def test_a_consultant_profile_names_the_pipe_layer(self, clean: Any, tmp_path: Path) -> None:
        document, truth = clean

        found = detected(document, "dxf", tmp_path, profile=Profile(layers=("FP-PIPE",)))

        assert found.pipe_key == "layer:FP-PIPE"
        assert not found.network.pipe_key_learnt
        assert lengths_by_dn(found) == pytest.approx(truth.lengths)


@pytest.mark.req("FR-VIS-03")
class TestOrientationAndPlace:
    @pytest.mark.parametrize("form", FORMS)
    def test_a_sidewall_head_says_which_way_it_faces(
        self, form: str, clean: Any, tmp_path: Path
    ) -> None:
        document, _ = clean
        found = detected(document, form, tmp_path)

        sidewalls = [o for o in found.objects if o.object_type == "sprinkler_sidewall"]

        assert [o.orientation for o in sidewalls] == [90.0] * 4

    def test_a_symmetric_pdf_symbol_claims_no_orientation(self, clean: Any, tmp_path: Path) -> None:
        document, _ = clean
        found = detected(document, "pdf", tmp_path)

        pendents = [o for o in found.objects if o.object_type == "sprinkler_pendent"]

        assert {o.orientation for o in pendents} == {None}

    @pytest.mark.parametrize("form", FORMS)
    def test_every_detection_has_its_view_grid_reference_level_and_evidence(
        self, form: str, clean: Any, tmp_path: Path
    ) -> None:
        document, _ = clean
        found = detected(document, form, tmp_path)

        for item in [*found.objects, *found.runs]:
            item.calibrated_confidence = item.raw_confidence  # applied later, in the service
            assert missing_fields(item) == [], item
        assert {o.level for o in found.objects} == {"L05"}
        assert all(o.grid_reference.startswith("Grid ") for o in found.objects)

    def test_a_run_on_an_unverified_view_has_no_length(self, tmp_path: Path) -> None:
        """FR-VIS-05 still holds: the network is found, but nothing is measured."""
        from dataclasses import replace

        document, _ = network_plan()
        table, page, source_views = sheet(document, "dxf", tmp_path)
        verified = views_of(table, page, "1:100", source_views)  # type: ignore[arg-type]
        unverified = [replace(view, denominator=None) for view in verified]
        placed, excluded = placed_from_legend(table, page, type_of)  # type: ignore[arg-type]

        found = detect(table, placed, unverified, excluded=excluded)

        assert found.runs and all(run.length_mm is None for run in found.runs)
        assert all(run.paper_length_mm > 0 for run in found.runs)

    def test_a_symbol_off_the_network_scores_lower(self, tmp_path: Path) -> None:
        document, _ = network_plan(distractors=6, seed=3)
        found = detected(document, "dxf", tmp_path)

        on = [o.raw_confidence for o in found.objects if o.features.get("on_network") is True]
        off = [o.raw_confidence for o in found.objects if o.features.get("on_network") is False]

        assert off and max(off) < min(on)
