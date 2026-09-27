"""The `doc_classification` eval suite and its metrics (FR-DOC-02)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from firebid.evals.doc_classification import FORMS, TitleBlockPredictor, generate
from firebid.evals.metrics import drawing_number_accuracy, revision_accuracy
from firebid.evals.prediction import SheetPrediction, TenderPrediction
from firebid.evals.runner import markdown_report, run_suite
from firebid.evals.schema import GoldenSet, InputClass, SheetTruth, TenderTruth

pytestmark = pytest.mark.req("FR-DOC-02")


def truth(*pairs: tuple[str, str]) -> TenderTruth:
    return TenderTruth(
        tender_id="T",
        consultant="C",
        input_class=InputClass.VECTOR_PDF,
        sheets=tuple(
            SheetTruth(sheet_number=number, revision=revision, input_class=InputClass.VECTOR_PDF)
            for number, revision in pairs
        ),
    )


def predicted(*pairs: tuple[str, str]) -> TenderPrediction:
    return TenderPrediction(
        tender_id="T",
        sheets=tuple(
            SheetPrediction(sheet_number=number, revision=revision) for number, revision in pairs
        ),
    )


class TestMetrics:
    def test_the_number_can_be_right_when_the_revision_is_not(self) -> None:
        expected = truth(("FP-1", "R04"), ("FP-2", "A"))
        read = predicted(("FP-1", "R03"), ("FP-2", "A"))

        assert drawing_number_accuracy(expected, read).value == 1.0
        assert revision_accuracy(expected, read).value == 0.5

    def test_a_missed_sheet_counts_against_both(self) -> None:
        expected = truth(("FP-1", "R04"), ("FP-2", "A"))
        read = predicted(("FP-1", "R04"))

        assert drawing_number_accuracy(expected, read).value == 0.5
        assert revision_accuracy(expected, read).value == 0.5


class TestTheFixtures:
    def test_every_form_holds_the_same_drawings(self, tmp_path: Path) -> None:
        suite = generate(tmp_path, seed=3, sheets=4)

        assert len(suite.golden_set.tenders) == len(FORMS)
        sets = [
            {(sheet.sheet_number, sheet.revision) for sheet in tender.sheets}
            for tender in suite.golden_set.tenders
        ]
        assert all(drawings == sets[0] for drawings in sets)
        for tender in suite.golden_set.tenders:
            assert len(suite.files[tender.tender_id]) == len(tender.sheets)

    def test_some_drawings_are_issued_twice(self, tmp_path: Path) -> None:
        tender = generate(tmp_path, seed=1, sheets=8).golden_set.tenders[0]

        numbers = [sheet.sheet_number for sheet in tender.sheets]
        assert len(numbers) > len(set(numbers)), "the suite must test superseded revisions"


class TestThePredictor:
    def test_cad_and_text_layer_title_blocks_are_read(self, tmp_path: Path) -> None:
        """The population the ≥95% target is for: vector title blocks."""
        suite = generate(tmp_path, seed=1, sheets=4)
        vector = GoldenSet(
            name="vector",
            tenders=tuple(
                tender
                for tender in suite.golden_set.tenders
                if tender.tender_id in ("SYNTH-DOC-CAD", "SYNTH-DOC-PDF-TEXT")
            ),
        )

        result = run_suite(vector, TitleBlockPredictor(suite.files, use_ocr=False))

        assert result.overall["drawing_number_accuracy"] == 1.0
        assert result.overall["revision_accuracy"] == 1.0
        assert result.overall["sheet_classification_accuracy"] == 1.0, "superseded ones too"

    @pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is not installed")
    def test_outlined_title_blocks_are_read_by_ocr(self, tmp_path: Path) -> None:
        suite = generate(tmp_path, seed=1, sheets=3)
        outlined = GoldenSet(
            name="outlined",
            tenders=tuple(
                tender
                for tender in suite.golden_set.tenders
                if tender.tender_id == "SYNTH-DOC-PDF-OUTLINED"
            ),
        )

        result = run_suite(outlined, TitleBlockPredictor(suite.files))

        assert result.overall["drawing_number_accuracy"] == 1.0

    def test_the_report_shows_only_what_the_suite_measures(self, tmp_path: Path) -> None:
        suite = generate(tmp_path, seed=1, sheets=2)
        cad = GoldenSet(name="cad", tenders=suite.golden_set.tenders[:1])
        result = run_suite(cad, TitleBlockPredictor(suite.files, use_ocr=False))

        report = markdown_report(result, metrics=("drawing_number_accuracy", "revision_accuracy"))

        assert "sprinkler_count_accuracy" not in report
        assert "## By tender" in report
        assert "SYNTH-DOC-CAD" in report
