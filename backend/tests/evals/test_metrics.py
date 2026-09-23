"""Every Phase 1 metric against hand-computed examples (§14, FR-LRN-01).

The numbers here are worked out by hand in the comments, not produced by the code being
tested. A metric test that asserts what the implementation happens to return is a test of
nothing.
"""

from __future__ import annotations

import pytest

from firebid.evals.metrics import (
    HIGHER_IS_BETTER,
    aggregate,
    boq_mapping_accuracy,
    calibration,
    count_outcomes,
    duplicate_detection_rate,
    false_detection_rate,
    missed_item_rate,
    pipe_length_error,
    score_tender,
    sheet_classification_accuracy,
    sprinkler_count_accuracy,
)
from firebid.evals.prediction import (
    BoqMappingPrediction,
    CountPrediction,
    LengthPrediction,
    SheetPrediction,
    TenderPrediction,
)
from firebid.evals.schema import (
    BoqLineTruth,
    InputClass,
    ObjectCount,
    ObjectType,
    PipeLength,
    RevisionStatus,
    SheetTruth,
    TenderTruth,
)

pytestmark = pytest.mark.req("FR-LRN-01")

PENDENT = ObjectType.SPRINKLER_PENDENT
UPRIGHT = ObjectType.SPRINKLER_UPRIGHT
HOSE_REEL = ObjectType.HOSE_REEL


def sheet(
    number: str = "A-01",
    *,
    counts: dict[ObjectType, int] | None = None,
    lengths: dict[int, int] | None = None,
    revision: str = "R01",
    status: RevisionStatus = RevisionStatus.CURRENT,
    not_to_scale: bool = False,
    duplicates_of: tuple[str, ...] = (),
) -> SheetTruth:
    return SheetTruth(
        sheet_number=number,
        revision=revision,
        status=status,
        input_class=InputClass.VECTOR_PDF,
        not_to_scale=not_to_scale,
        counts=tuple(
            ObjectCount(object_type=kind, count=value) for kind, value in (counts or {}).items()
        ),
        pipe_lengths=tuple(
            PipeLength(nominal_diameter_mm=diameter, length_mm=value)
            for diameter, value in (lengths or {}).items()
        ),
        duplicates_of=duplicates_of,
    )


def tender(*sheets: SheetTruth, boq: tuple[BoqLineTruth, ...] = ()) -> TenderTruth:
    return TenderTruth(
        tender_id="T-1",
        consultant="Consultant",
        input_class=InputClass.VECTOR_PDF,
        sheets=sheets or (sheet(),),
        boq_lines=boq,
    )


def predicted(
    number: str = "A-01",
    *,
    counts: dict[ObjectType, int] | None = None,
    lengths: dict[int, int] | None = None,
    revision: str = "R01",
    status: RevisionStatus = RevisionStatus.CURRENT,
    duplicates_of: tuple[str, ...] = (),
    confidence: float = 1.0,
) -> SheetPrediction:
    return SheetPrediction(
        sheet_number=number,
        revision=revision,
        status=status,
        counts=tuple(
            CountPrediction(object_type=kind, count=value, confidence=confidence)
            for kind, value in (counts or {}).items()
        ),
        pipe_lengths=tuple(
            LengthPrediction(nominal_diameter_mm=diameter, length_mm=value)
            for diameter, value in (lengths or {}).items()
        ),
        duplicates_of=duplicates_of,
    )


def answer(
    *sheets: SheetPrediction, boq: tuple[BoqMappingPrediction, ...] = ()
) -> TenderPrediction:
    return TenderPrediction(tender_id="T-1", sheets=sheets, boq_mappings=boq)


class TestSprinklerCountAccuracy:
    def test_an_exact_count_scores_one(self) -> None:
        # 1 - |100 - 100| / 100 = 1.0
        results = sprinkler_count_accuracy(
            tender(sheet(counts={PENDENT: 100})), answer(predicted(counts={PENDENT: 100}))
        )
        assert results[0].value == pytest.approx(1.0)

    def test_two_short_of_a_hundred_scores_ninety_eight_percent(self) -> None:
        # 1 - |98 - 100| / 100 = 0.98, which is exactly the §14 target
        results = sprinkler_count_accuracy(
            tender(sheet(counts={PENDENT: 100})), answer(predicted(counts={PENDENT: 98}))
        )
        assert results[0].value == pytest.approx(0.98)

    def test_sprinkler_types_are_counted_together(self) -> None:
        # verified 60 + 40 = 100; predicted 100 pendent = 100. Type confusion is a separate
        # concern from counting, and this metric is about counting.
        results = sprinkler_count_accuracy(
            tender(sheet(counts={PENDENT: 60, UPRIGHT: 40})),
            answer(predicted(counts={PENDENT: 100})),
        )
        assert results[0].value == pytest.approx(1.0)

    def test_non_sprinklers_are_ignored(self) -> None:
        # verified sprinklers 10; the 4 hose reels belong to no sprinkler count
        results = sprinkler_count_accuracy(
            tender(sheet(counts={PENDENT: 10, HOSE_REEL: 4})),
            answer(predicted(counts={PENDENT: 10, HOSE_REEL: 0})),
        )
        assert results[0].value == pytest.approx(1.0)

    def test_all_items_missed_scores_zero(self) -> None:
        # 1 - |0 - 100| / 100 = 0.0
        results = sprinkler_count_accuracy(
            tender(sheet(counts={PENDENT: 100})), answer(predicted(counts={PENDENT: 0}))
        )
        assert results[0].value == pytest.approx(0.0)

    def test_a_sheet_not_predicted_at_all_scores_zero(self) -> None:
        results = sprinkler_count_accuracy(tender(sheet(counts={PENDENT: 50})), answer())
        assert results[0].value == pytest.approx(0.0)

    def test_a_wild_overcount_is_clamped_at_zero(self) -> None:
        # 1 - |300 - 100| / 100 = -1.0, clamped to 0.0 so one sheet cannot drag a mean into
        # nonsense. It is wrong, not doubly wrong.
        results = sprinkler_count_accuracy(
            tender(sheet(counts={PENDENT: 100})), answer(predicted(counts={PENDENT: 300}))
        )
        assert results[0].value == pytest.approx(0.0)

    def test_zero_verified_is_undefined_not_perfect(self) -> None:
        """A sheet with no sprinklers must not score 100% and flatter the average."""
        results = sprinkler_count_accuracy(
            tender(sheet(counts={HOSE_REEL: 2})), answer(predicted(counts={HOSE_REEL: 2}))
        )
        assert results[0].value is None
        assert "no sprinklers" in (results[0].undefined_reason or "")

    def test_superseded_sheets_are_not_scored(self) -> None:
        """The platform is judged on the current drawing, not the one it replaced."""
        truth = tender(
            sheet("A-01", counts={PENDENT: 10}, revision="R02"),
            sheet("A-01", counts={PENDENT: 99}, revision="R01", status=RevisionStatus.SUPERSEDED),
        )
        results = sprinkler_count_accuracy(truth, answer(predicted(counts={PENDENT: 10})))
        assert len(results) == 1
        assert results[0].value == pytest.approx(1.0)


class TestPipeLengthError:
    def test_an_exact_length_has_no_error(self) -> None:
        results = pipe_length_error(
            tender(sheet(lengths={150: 100_000})), answer(predicted(lengths={150: 100_000}))
        )
        assert results[0].value == pytest.approx(0.0)

    def test_four_percent_over_is_four_percent_error(self) -> None:
        # |104000 - 100000| / 100000 = 0.04, inside the ±5% target
        results = pipe_length_error(
            tender(sheet(lengths={150: 100_000})), answer(predicted(lengths={150: 104_000}))
        )
        assert results[0].value == pytest.approx(0.04)

    def test_under_measuring_is_the_same_error_as_over(self) -> None:
        results = pipe_length_error(
            tender(sheet(lengths={150: 100_000})), answer(predicted(lengths={150: 96_000}))
        )
        assert results[0].value == pytest.approx(0.04)

    def test_each_diameter_is_measured_separately(self) -> None:
        # DN150 exact; DN100 out by 50%. Averaging inside a diameter would hide the second.
        results = pipe_length_error(
            tender(sheet(lengths={150: 100_000, 100: 10_000})),
            answer(predicted(lengths={150: 100_000, 100: 5_000})),
        )
        by_name = {result.name: result.value for result in results}
        assert by_name["pipe_length_error[A-01/DN150]"] == pytest.approx(0.0)
        assert by_name["pipe_length_error[A-01/DN100]"] == pytest.approx(0.5)

    def test_a_not_to_scale_sheet_is_excluded(self) -> None:
        """No scale means no measurable length, so a miss there is not a failure to measure."""
        results = pipe_length_error(
            tender(sheet(lengths={150: 100_000}, not_to_scale=True)), answer()
        )
        assert results[0].value is None
        assert "not to scale" in (results[0].undefined_reason or "")

    def test_missing_the_pipe_entirely_is_a_hundred_percent_error(self) -> None:
        results = pipe_length_error(tender(sheet(lengths={150: 100_000})), answer())
        assert results[0].value == pytest.approx(1.0)


class TestMissedAndFalse:
    def test_nothing_missed(self) -> None:
        result = missed_item_rate(
            tender(sheet(counts={PENDENT: 100})), answer(predicted(counts={PENDENT: 100}))
        )
        assert result.value == pytest.approx(0.0)

    def test_five_of_a_hundred_missed(self) -> None:
        # 5 / 100 = 0.05, exactly the §14 ceiling
        result = missed_item_rate(
            tender(sheet(counts={PENDENT: 100})), answer(predicted(counts={PENDENT: 95}))
        )
        assert result.value == pytest.approx(0.05)

    def test_everything_missed(self) -> None:
        result = missed_item_rate(tender(sheet(counts={PENDENT: 40, HOSE_REEL: 10})), answer())
        assert result.value == pytest.approx(1.0)
        assert result.numerator == 50

    def test_an_overcount_does_not_offset_a_miss(self) -> None:
        """20 found where 10 exist does not make up for 10 missed elsewhere."""
        truth = tender(sheet("A-01", counts={PENDENT: 10}), sheet("A-02", counts={PENDENT: 10}))
        prediction = answer(
            predicted("A-01", counts={PENDENT: 20}), predicted("A-02", counts={PENDENT: 0})
        )
        assert missed_item_rate(truth, prediction).value == pytest.approx(0.5)

    def test_no_verified_items_is_undefined(self) -> None:
        assert missed_item_rate(tender(sheet()), answer()).value is None

    def test_no_false_detections(self) -> None:
        result = false_detection_rate(
            tender(sheet(counts={PENDENT: 100})), answer(predicted(counts={PENDENT: 100}))
        )
        assert result.value == pytest.approx(0.0)

    def test_ten_detections_where_eight_exist(self) -> None:
        # excess 2 / detected 10 = 0.2
        result = false_detection_rate(
            tender(sheet(counts={PENDENT: 8})), answer(predicted(counts={PENDENT: 10}))
        )
        assert result.value == pytest.approx(0.2)

    def test_detecting_something_on_a_sheet_with_none_is_all_false(self) -> None:
        result = false_detection_rate(
            tender(sheet(counts={HOSE_REEL: 1})), answer(predicted(counts={PENDENT: 7}))
        )
        assert result.value == pytest.approx(1.0)

    def test_detecting_nothing_is_undefined_not_perfect(self) -> None:
        """A predictor that finds nothing has no false detections, which is not a good score."""
        assert false_detection_rate(tender(sheet(counts={PENDENT: 10})), answer()).value is None


class TestDuplicates:
    def test_both_duplicates_found(self) -> None:
        truth = tender(
            sheet("A-01"),
            sheet("A-02", duplicates_of=("A-01",)),
            sheet("A-03", duplicates_of=("A-01",)),
        )
        prediction = answer(
            predicted("A-02", duplicates_of=("A-01",)), predicted("A-03", duplicates_of=("A-01",))
        )
        assert duplicate_detection_rate(truth, prediction).value == pytest.approx(1.0)

    def test_one_of_two_found(self) -> None:
        truth = tender(
            sheet("A-01"),
            sheet("A-02", duplicates_of=("A-01",)),
            sheet("A-03", duplicates_of=("A-01",)),
        )
        prediction = answer(predicted("A-02", duplicates_of=("A-01",)))
        assert duplicate_detection_rate(truth, prediction).value == pytest.approx(0.5)

    def test_flagging_a_duplicate_that_is_not_one_does_not_help(self) -> None:
        truth = tender(sheet("A-01"), sheet("A-02", duplicates_of=("A-01",)))
        prediction = answer(
            predicted("A-02", duplicates_of=("A-01",)),
            predicted("A-01", duplicates_of=("A-02",)),  # invented
        )
        assert duplicate_detection_rate(truth, prediction).value == pytest.approx(1.0)

    def test_no_seeded_duplicates_is_undefined(self) -> None:
        assert duplicate_detection_rate(tender(sheet()), answer()).value is None


class TestSheetClassification:
    def test_everything_right(self) -> None:
        truth = tender(
            sheet("A-01", revision="R02"),
            sheet("A-01", revision="R01", status=RevisionStatus.SUPERSEDED),
        )
        prediction = answer(
            predicted("A-01", revision="R02"),
            predicted("A-01", revision="R01", status=RevisionStatus.SUPERSEDED),
        )
        assert sheet_classification_accuracy(truth, prediction).value == pytest.approx(1.0)

    def test_the_right_number_at_the_wrong_revision_is_wrong(self) -> None:
        """Not two-thirds correct — it is the wrong drawing."""
        truth = tender(sheet("A-01", revision="R02"))
        prediction = answer(predicted("A-01", revision="R01"))
        assert sheet_classification_accuracy(truth, prediction).value == pytest.approx(0.0)

    def test_calling_a_superseded_sheet_current_is_wrong(self) -> None:
        """This is the error that gets an out-of-date drawing priced."""
        truth = tender(
            sheet("A-01", revision="R02"),
            sheet("A-01", revision="R01", status=RevisionStatus.SUPERSEDED),
        )
        prediction = answer(predicted("A-01", revision="R02"), predicted("A-01", revision="R01"))
        assert sheet_classification_accuracy(truth, prediction).value == pytest.approx(0.5)


class TestBoqMapping:
    def _lines(self) -> tuple[BoqLineTruth, ...]:
        return (
            BoqLineTruth(
                line_reference="1.1",
                description="Sprinklers",
                unit="nr",
                quantity=100,
                maps_to=PENDENT,
            ),
            BoqLineTruth(
                line_reference="1.2",
                description="Hose reels",
                unit="nr",
                quantity=4,
                maps_to=HOSE_REEL,
            ),
            BoqLineTruth(
                line_reference="9.9",
                description="Preliminaries",
                unit="item",
                quantity=1,
                maps_to=None,
            ),
        )

    def test_all_three_right_including_the_unmapped_one(self) -> None:
        prediction = answer(
            boq=(
                BoqMappingPrediction(line_reference="1.1", maps_to=PENDENT),
                BoqMappingPrediction(line_reference="1.2", maps_to=HOSE_REEL),
                BoqMappingPrediction(line_reference="9.9", maps_to=None),
            )
        )
        result = boq_mapping_accuracy(tender(sheet(), boq=self._lines()), prediction)
        assert result.value == pytest.approx(1.0)

    def test_forcing_a_mapping_onto_preliminaries_is_wrong(self) -> None:
        """Scoring unmapped lines is what exposes a model that maps everything."""
        prediction = answer(
            boq=(
                BoqMappingPrediction(line_reference="1.1", maps_to=PENDENT),
                BoqMappingPrediction(line_reference="1.2", maps_to=HOSE_REEL),
                BoqMappingPrediction(line_reference="9.9", maps_to=PENDENT),
            )
        )
        result = boq_mapping_accuracy(tender(sheet(), boq=self._lines()), prediction)
        assert result.value == pytest.approx(2 / 3)

    def test_a_line_left_unanswered_is_wrong_unless_it_maps_to_nothing(self) -> None:
        prediction = answer(boq=(BoqMappingPrediction(line_reference="9.9", maps_to=None),))
        result = boq_mapping_accuracy(tender(sheet(), boq=self._lines()), prediction)
        assert result.value == pytest.approx(1 / 3)

    def test_no_boq_is_undefined(self) -> None:
        assert boq_mapping_accuracy(tender(sheet()), answer()).value is None


class TestCalibration:
    def test_a_perfectly_calibrated_model_scores_zero(self) -> None:
        # Ten claims at 0.9, nine of them right: said 0.9, was right 0.9, gap 0.
        outcomes = [(0.9, True)] * 9 + [(0.9, False)]
        value, bins = calibration(outcomes)
        assert value.value == pytest.approx(0.0, abs=1e-9)
        assert len(bins) == 1
        assert bins[0].count == 10

    def test_overconfidence_is_measured(self) -> None:
        # Ten claims at 1.0, five right: said 1.0, was right 0.5, gap 0.5.
        value, _ = calibration([(1.0, True)] * 5 + [(1.0, False)] * 5)
        assert value.value == pytest.approx(0.5)

    def test_underconfidence_is_measured_too(self) -> None:
        # Ten claims at 0.1, all right: said 0.1, was right 1.0, gap 0.9.
        value, _ = calibration([(0.1, True)] * 10)
        assert value.value == pytest.approx(0.9)

    def test_bins_are_weighted_by_how_many_claims_fall_in_them(self) -> None:
        # 90 claims at 0.9 all right (gap 0.1), 10 at 0.1 all right (gap 0.9).
        # (90*0.1 + 10*0.9) / 100 = 0.18
        outcomes = [(0.9, True)] * 90 + [(0.1, True)] * 10
        value, _ = calibration(outcomes)
        assert value.value == pytest.approx(0.18)

    def test_a_confidence_of_exactly_one_is_binned(self) -> None:
        """1.0 sits on the top edge and would otherwise fall outside every bin."""
        _, bins = calibration([(1.0, True)])
        assert sum(bin_.count for bin_ in bins) == 1

    def test_no_claims_is_undefined(self) -> None:
        value, bins = calibration([])
        assert value.value is None
        assert bins == []

    def test_outcomes_come_from_whether_the_count_was_exactly_right(self) -> None:
        truth = tender(sheet(counts={PENDENT: 10}))
        prediction = answer(predicted(counts={PENDENT: 10}, confidence=0.8))
        assert count_outcomes(truth, prediction) == [(0.8, True)]

        wrong = answer(predicted(counts={PENDENT: 9}, confidence=0.8))
        assert count_outcomes(truth, wrong) == [(0.8, False)]


class TestAggregation:
    def test_undefined_values_are_excluded_and_counted(self) -> None:
        truth = tender(
            sheet("A-01", counts={PENDENT: 100}),
            sheet("A-02", counts={HOSE_REEL: 2}),  # no sprinklers: undefined
        )
        prediction = answer(
            predicted("A-01", counts={PENDENT: 100}), predicted("A-02", counts={HOSE_REEL: 2})
        )
        result = aggregate("x", sprinkler_count_accuracy(truth, prediction))
        assert result.value == pytest.approx(1.0)
        assert result.samples == 1
        assert result.excluded == 1
        assert sum(result.excluded_reasons.values()) == 1

    def test_everything_undefined_gives_no_value_rather_than_zero(self) -> None:
        truth = tender(sheet(counts={HOSE_REEL: 2}))
        result = aggregate("x", sprinkler_count_accuracy(truth, answer()))
        assert result.value is None
        assert result.samples == 0


class TestScoringAWholeTender:
    def test_a_perfect_prediction_scores_at_the_top_of_every_metric(self) -> None:
        truth = tender(
            sheet("A-01", counts={PENDENT: 100}, lengths={150: 50_000}),
            sheet("A-02", counts={PENDENT: 20}, duplicates_of=("A-01",)),
            boq=(
                BoqLineTruth(
                    line_reference="1.1",
                    description="Sprinklers",
                    unit="nr",
                    quantity=120,
                    maps_to=PENDENT,
                ),
            ),
        )
        prediction = answer(
            predicted("A-01", counts={PENDENT: 100}, lengths={150: 50_000}),
            predicted("A-02", counts={PENDENT: 20}, duplicates_of=("A-01",)),
            boq=(BoqMappingPrediction(line_reference="1.1", maps_to=PENDENT),),
        )

        score = score_tender(truth, prediction)
        values = score.named_values()
        assert values["sprinkler_count_accuracy"] == pytest.approx(1.0)
        assert values["pipe_length_error"] == pytest.approx(0.0)
        assert values["missed_item_rate"] == pytest.approx(0.0)
        assert values["false_detection_rate"] == pytest.approx(0.0)
        assert values["duplicate_detection_rate"] == pytest.approx(1.0)
        assert values["sheet_classification_accuracy"] == pytest.approx(1.0)
        assert values["boq_mapping_accuracy"] == pytest.approx(1.0)

    def test_every_metric_declares_which_direction_is_better(self) -> None:
        """The regression gate needs this; a metric without it cannot be gated."""
        score = score_tender(tender(sheet(counts={PENDENT: 1})), answer())
        assert set(score.named_values()) == set(HIGHER_IS_BETTER)
