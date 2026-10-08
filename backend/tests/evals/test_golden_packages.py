"""The committed golden reference packages (`docs/plan/TEST_STRATEGY.md`, section 4).

A synthetic package's expected values come from what its fixture generator draws. If the
generator changes, the package must change with it, in the same commit, or a comparison
would blame the platform for the fixture's change. These tests are that guard: they compare
a package with its generator, never with the platform's own reading of the drawings.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from firebid.evals import synthetic_qto as q
from firebid.evals.synthetic_network import NETWORK, TYPES

pytestmark = pytest.mark.req("FR-LRN-01")

GOLDEN = Path(__file__).resolve().parents[3] / "eval" / "golden" / "synthetic"
COMPARISONS = {"EXACT", "SEMANTIC", "TOLERANCE", "EVIDENCE", "COMPLETENESS"}
SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}


def package(test_case: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(
        (GOLDEN / test_case / "golden.json").read_text(encoding="utf-8")
    )
    return loaded


def stage(golden: dict[str, Any], stage_id: str) -> dict[str, Any]:
    (found,) = [one for one in golden["stages"] if one["stage_id"] == stage_id]
    expected: dict[str, Any] = found["expected_output"]
    return expected


@pytest.mark.parametrize("folder", sorted(path.name for path in GOLDEN.iterdir()))
def test_every_committed_package_has_its_three_files_and_a_well_formed_dataset(
    folder: str,
) -> None:
    for name in ("package.md", "golden.json", "manifest.json"):
        assert (GOLDEN / folder / name).is_file(), f"{folder} has no {name}"
    golden = package(folder)
    manifest = json.loads((GOLDEN / folder / "manifest.json").read_text(encoding="utf-8"))

    assert golden["test_case_id"] == manifest["test_case_id"] == folder
    assert manifest["kind"] == "synthetic", "a real tender's package is not committed"
    numbers = [one["stage_id"] for one in golden["stages"]]
    assert numbers == sorted(set(numbers)), "each stage once, in order"
    for one in golden["stages"]:
        assert one["stage_id"].startswith("STG-") and one["stage_name"]
        assert one["comparison_type"] in COMPARISONS
        assert one["severity_if_incorrect"] in SEVERITIES
        assert one["expected_output"] and one["mandatory_fields"]
    assert golden["final_output"]["expected_result"]


class TestTcSyn001:
    """The general arrangement, the enlarged plan and the riser schematic."""

    SHEETS = (
        ("FP-L05-201", q.general_arrangement),
        ("FP-L05-301", q.enlarged_plan),
        ("FP-SCH-001", q.riser_schematic),
    )

    def test_its_counts_and_positions_are_what_the_generator_draws(self) -> None:
        sheets = {one["sheet"]: one for one in stage(package("TC-SYN-001"), "STG-005")["sheets"]}

        assert list(sheets) == [number for number, _ in self.SHEETS]
        for number, make in self.SHEETS:
            drawn = make()[1]
            assert sheets[number]["counts"] == q.counted(drawn)
            placed = sorted(
                (TYPES[s.block], s.x, s.y) for s in drawn.symbols if TYPES[s.block] != "pipe"
            )
            listed = sorted(
                (one["object_type"], one["x_mm"], one["y_mm"])
                for one in sheets[number]["instances"]
            )
            assert listed == placed

    def test_its_pipe_lengths_are_what_the_generator_draws(self) -> None:
        sheets = {one["sheet"]: one for one in stage(package("TC-SYN-001"), "STG-006")["sheets"]}

        for number, make in self.SHEETS:
            lengths: dict[str, float] = {}
            for pipe in make()[1].pipes:
                size = str(pipe.dn)
                lengths[size] = lengths.get(size, 0.0) + math.hypot(
                    pipe.x1 - pipe.x0, pipe.y1 - pipe.y0
                )
            assert sheets[number]["length_mm_by_dn"] == pytest.approx(lengths)

    def test_its_legend_is_the_consultant_s(self) -> None:
        rows = stage(package("TC-SYN-001"), "STG-004")["rows"]

        assert [(row["block"], row["description"], row["object_type"]) for row in rows] == [
            (symbol.block, symbol.description, symbol.object_type) for symbol in NETWORK.symbols
        ]

    def test_its_takeoff_counts_the_installation_once(self) -> None:
        golden = package("TC-SYN-001")
        whole = q.counted(q.general_arrangement()[1])
        counted = {
            item["item"]: item["quantity"]
            for item in stage(golden, "STG-007")["drawn_items"]
            if item["unit"] == "no"
        }

        assert counted == whole
        final = golden["final_output"]["expected_result"]
        assert final["sprinklers_total"] == sum(
            count for kind, count in whole.items() if kind.startswith("sprinkler_")
        )
        # The trap the case is there to catch: the sheets added together are more.
        every = [q.counted(make()[1]) for _, make in self.SHEETS]
        assert final["sum_of_the_sheets_before_duplicates"] == {
            kind: sum(sheet.get(kind, 0) for sheet in every)
            for kind in ("sprinkler_pendent", "gate_valve", "check_valve")
        }
