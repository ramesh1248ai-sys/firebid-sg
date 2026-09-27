"""Calibrated detection confidence (FR-VIS-09): a detection scored 0.9 is right about 90% of
the time.

The committed calibration (`config/calibration/p1_detection.json`, from `firebid-eval
calibrate`) is checked here on synthetic installations it was never fitted or checked on,
with the mistakes that make calibration meaningful: near-miss and decoy symbols, missing,
wrong and contradictory size annotations.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from firebid.drawings import calibration
from firebid.drawings.calibration import Isotonic, fit
from firebid.evals.detection_calibration import ECE_TOLERANCE, outcomes_for, type_of_factory
from firebid.evals.metrics import calibration as ece

pytestmark = pytest.mark.req("FR-VIS-09")

# Seeds neither the fit (1000-1059) nor its hold-out (1060-1099) used.
UNSEEN = range(2_000, 2_020)


class TestIsotonic:
    def test_it_only_rises(self) -> None:
        fitted = fit([0.1, 0.2, 0.3, 0.4, 0.5, 0.6], [True, False, True, False, True, True])

        assert list(fitted.y) == sorted(fitted.y)

    def test_tied_scores_are_one_point_scored_by_their_share_right(self) -> None:
        """Many detections share a score exactly; the map must give their share right."""
        fitted = fit([0.9] * 10 + [0.2] * 10, [True] * 7 + [False] * 3 + [False] * 10)

        assert fitted(0.9) == pytest.approx(0.7)
        assert fitted(0.2) == pytest.approx(0.0)

    def test_perfectly_ordered_outcomes_are_reproduced(self) -> None:
        fitted = fit([0.1, 0.2, 0.8, 0.9], [False, False, True, True])

        assert (fitted(0.1), fitted(0.9)) == (0.0, 1.0)

    def test_with_nothing_to_fit_it_changes_nothing(self) -> None:
        assert Isotonic((), ())(0.42) == 0.42


class TestTheCommittedCalibration:
    def test_it_is_fitted_and_versioned(self) -> None:
        loaded = calibration.load()

        assert loaded.version != "uncalibrated"
        assert set(loaded.maps) == set(calibration.FAMILIES)
        assert loaded.report["within_tolerance"] is True

    def test_expected_calibration_error_on_unseen_installations_is_within_tolerance(
        self,
    ) -> None:
        loaded = calibration.load()
        type_of = type_of_factory()
        outcomes = [o for seed in UNSEEN for o in outcomes_for(seed, type_of)]

        calibrated = [(loaded.apply(o.family, o.raw), o.correct) for o in outcomes]
        raw = [(o.raw, o.correct) for o in outcomes]
        value, _ = ece(calibrated)

        assert len(outcomes) > 800
        assert value.value is not None and value.value <= ECE_TOLERANCE
        assert value.value < (ece(raw)[0].value or 1.0), "calibration improves on the raw score"

    def test_without_a_fitted_file_confidence_is_marked_uncalibrated(self, tmp_path: Path) -> None:
        loaded = calibration.load(tmp_path / "missing.json")

        assert loaded.version == "uncalibrated"
        assert loaded.apply("symbol", 0.3) == 0.3
