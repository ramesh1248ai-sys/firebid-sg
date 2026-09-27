"""Expected accuracy per sheet, and the manual takeoff flag (FR-DOC-06)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from firebid.drawings.quality import Measures, Policy, assess, current_policy, policies, scale_state

pytestmark = pytest.mark.req("FR-DOC-06")

POLICY = Policy(
    effective_from=date(2026, 1, 1),
    good_dpi=300,
    good_ocr=0.8,
    floor_dpi=200,
    floor_ocr=0.6,
    bands={"high": "h", "medium": "m", "low": "l"},
)


class TestBands:
    def test_vector_with_a_stated_scale_is_high(self) -> None:
        verdict = assess(Measures("vector", scale="stated"), POLICY)
        assert (verdict.band, verdict.manual_takeoff_recommended) == ("high", False)

    def test_vector_without_a_scale_cannot_promise_lengths(self) -> None:
        verdict = assess(Measures("vector", scale="not_to_scale"), POLICY)
        assert verdict.band == "medium"
        assert any("not to scale" in reason for reason in verdict.reasons)

    def test_a_sharp_legible_scan_with_a_scale_is_medium(self) -> None:
        verdict = assess(Measures("raster", dpi=400, ocr=0.93, scale="stated"), POLICY)
        assert (verdict.band, verdict.manual_takeoff_recommended) == ("medium", False)

    def test_a_scan_between_the_floor_and_good_is_low_but_not_refused(self) -> None:
        verdict = assess(Measures("raster", dpi=240, ocr=0.85, scale="stated"), POLICY)
        assert (verdict.band, verdict.manual_takeoff_recommended) == ("low", False)

    def test_a_scan_below_the_resolution_floor_needs_a_person(self) -> None:
        verdict = assess(Measures("raster", dpi=72, scale="stated"), POLICY)
        assert (verdict.band, verdict.manual_takeoff_recommended) == ("low", True)
        assert "72 dpi, below 200" in verdict.reasons

    def test_an_illegible_scan_needs_a_person_whatever_its_resolution(self) -> None:
        verdict = assess(Measures("raster", dpi=600, ocr=0.4, scale="stated"), POLICY)
        assert verdict.manual_takeoff_recommended

    def test_a_mixed_sheet_is_judged_by_its_scan(self) -> None:
        assert assess(Measures("mixed", dpi=100, scale="stated"), POLICY).manual_takeoff_recommended

    def test_every_verdict_says_what_to_expect(self) -> None:
        assert assess(Measures("vector", scale="stated"), POLICY).expectation == "h"


class TestScale:
    @pytest.mark.parametrize(
        ("text", "state"),
        [
            ("1:100", "stated"),
            ("1:50@A1", "stated"),
            ("NTS", "not_to_scale"),
            ("N.T.S.", "not_to_scale"),
            (None, "absent"),
            ("AS SHOWN", "absent"),
        ],
    )
    def test_states(self, text: str | None, state: str) -> None:
        assert scale_state(text) == state


class TestConfiguration:
    def test_the_shipped_policy_loads(self) -> None:
        policy = current_policy(date(2026, 9, 27))
        assert policy.floor_dpi == 200
        assert set(policy.bands) == {"high", "medium", "low"}

    def test_the_policy_in_force_is_the_latest_that_has_started(self, tmp_path: Path) -> None:
        path = tmp_path / "quality.yaml"
        path.write_text(
            "policies:\n"
            + "".join(
                f"  - effective_from: {start}\n    raster: {{good_dpi: 300, good_ocr: 0.8, "
                f"floor_dpi: {floor}, floor_ocr: 0.6}}\n    bands: {{high: h}}\n"
                for start, floor in (("2026-01-01", 200), ("2027-01-01", 250))
            ),
            encoding="utf-8",
        )
        loaded = policies.__wrapped__(path)
        assert [policy.floor_dpi for policy in loaded] == [200, 250]
