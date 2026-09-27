"""Reading title blocks deterministically (FR-DOC-02).

The synthetic sheets are read through the same text extraction the sandbox uses, from DXF
entities and from a PDF text layer. Hand-built spans cover the layouts the generator does not
draw: an oldest-first revision history, inline labels, and a block with no labels at all.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from ezdxf.document import Drawing

from firebid.drawings.title_block import (
    CANDIDATES,
    DEFAULT_THRESHOLD,
    Box,
    Field,
    Span,
    TitleBlockReading,
    discipline_of,
    fingerprint,
    learn,
    ocr_digits,
    parse_date,
    read,
    read_with_layout,
    similarity,
)
from firebid.evals import synthetic
from firebid.evals.schema import SheetTruth
from firebid.parsing.text import dxf_text, ocr_text, pdf_text

pytestmark = pytest.mark.req("FR-DOC-02")

SHEETS = {
    "general arrangement": lambda: synthetic.general_arrangement(),
    "earlier revision": lambda: synthetic.general_arrangement(revision="R03"),
    "enlarged plan": lambda: synthetic.enlarged_plan(),
    "not to scale": lambda: synthetic.not_to_scale_sheet(),
    "legend": lambda: synthetic.legend_sheet(),
    "letter revision": lambda: synthetic.general_arrangement(revision="C"),
    "two-digit construction revision": lambda: synthetic.general_arrangement(revision="C12"),
}


def reading_of(data: dict[str, object]) -> TitleBlockReading:
    spans = [Span(*span) for span in data["spans"]]  # type: ignore[attr-defined]
    return read(spans, Box(*data["page"]))  # type: ignore[misc]


def from_dxf(document: Drawing) -> TitleBlockReading:
    return reading_of(dxf_text(synthetic.dxf_bytes(document), None))


def from_pdf(document: Drawing, tmp_path: Path) -> TitleBlockReading:
    pdf = synthetic.write_pdf(document, tmp_path / "sheet.pdf", live_text=True)
    return reading_of(pdf_text(pdf.read_bytes(), 0))


def span(text: str, x: float, y: float, height: float = 2.5, confidence: float = 1.0) -> Span:
    return Span(text, x, y, x + 0.7 * height * len(text), y + height, confidence)


PAGE = Box(0, 0, 841, 594)  # A1, in millimetres


class TestTheSyntheticSheets:
    @pytest.mark.parametrize("name", list(SHEETS))
    def test_a_dxf_title_block_is_read(self, name: str) -> None:
        document, truth = SHEETS[name]()

        reading = from_dxf(document)

        assert_identified(reading, truth)

    @pytest.mark.parametrize("name", list(SHEETS))
    def test_a_pdf_text_layer_is_read(self, name: str, tmp_path: Path) -> None:
        document, truth = SHEETS[name]()

        reading = from_pdf(document, tmp_path)

        assert_identified(reading, truth)

    def test_the_other_fields_are_read_too(self, tmp_path: Path) -> None:
        document, _ = synthetic.general_arrangement()

        reading = from_pdf(document, tmp_path)

        assert reading.value(Field.TITLE) == "FIRE SPRINKLER LAYOUT"
        assert reading.value(Field.SCALE) == "1:100"
        assert reading.revision_date == synthetic.revision_history("R04")[-1][1]
        assert reading.value(Field.LEVEL) == "L05"
        assert reading.discipline == "fire protection"

    def test_the_history_table_is_listed_but_not_taken_as_the_revision(self) -> None:
        document, _ = synthetic.general_arrangement(revision="R04")

        reading = from_dxf(document)

        assert reading.history == ("R04", "R03", "R02", "R01")
        assert reading.value(Field.REVISION) == "R04"


def assert_identified(reading: TitleBlockReading, truth: SheetTruth) -> None:
    assert reading.value(Field.SHEET_NUMBER) == truth.sheet_number
    assert reading.value(Field.REVISION) == truth.revision
    assert not reading.needs_help(DEFAULT_THRESHOLD), "a clean vector title block needs no help"


class TestLayoutsTheGeneratorDoesNotDraw:
    def test_an_oldest_first_history_does_not_decide_the_revision(self) -> None:
        """The trap: a reader taking the history row nearest the REV label would say A."""
        spans = [
            span("REV", 700, 470, 2),
            span("DATE", 715, 470, 2),
            span("DESCRIPTION", 740, 470, 2),
            span("A", 700, 475),
            span("01.03.2026", 715, 475),
            span("TENDER ISSUE", 740, 475),
            span("B", 700, 480),
            span("15.03.2026", 715, 480),
            span("RE-ISSUED FOR TENDER", 740, 480),
            span("DRAWING NO.", 620, 560, 2),
            span("FP-B1-110", 620, 572, 4),
            span("REV", 720, 560, 2),
            span("B", 720, 572, 4),
        ]

        reading = read(spans, PAGE)

        assert reading.history == ("A", "B")
        assert reading.value(Field.REVISION) == "B"
        assert reading.fields[Field.REVISION].how == "label"

    def test_labels_and_values_in_one_span_are_split(self) -> None:
        spans = [
            span("DWG NO: FP-L02-105", 640, 560),
            span("REV: P02", 640, 566),
            span("SCALE 1:200 @ A1", 640, 572),
        ]

        reading = read(spans, PAGE)

        assert reading.value(Field.SHEET_NUMBER) == "FP-L02-105"
        assert reading.value(Field.REVISION) == "P02"
        assert reading.value(Field.SCALE) == "1:200@A1"

    def test_a_value_beside_its_label_on_the_same_line_is_read(self) -> None:
        spans = [span("DRAWING NO.", 620, 560, 2), span("FP-RF-001", 650, 559.5, 3)]

        reading = read(spans, PAGE)

        assert reading.value(Field.SHEET_NUMBER) == "FP-RF-001"
        assert reading.value(Field.LEVEL) == "RF"

    def test_a_number_with_no_label_is_found_by_its_pattern_but_needs_help(self) -> None:
        spans = [span("FP-L03-201", 700, 570, 5), span("SPRINKLER LAYOUT", 700, 560)]

        reading = read(spans, PAGE)

        assert reading.value(Field.SHEET_NUMBER) == "FP-L03-201"
        assert reading.fields[Field.SHEET_NUMBER].how == "pattern"
        assert reading.needs_help(), "no label and no revision: a person or the model checks it"

    def test_a_revision_only_in_the_history_is_a_guess(self) -> None:
        spans = [
            span("REV", 700, 470, 2),
            span("DESCRIPTION", 740, 470, 2),
            span("C1", 700, 475),
            span("CONSTRUCTION ISSUE", 740, 475),
            span("DRAWING NO.", 620, 560, 2),
            span("FP-L01-001", 620, 572, 4),
        ]

        reading = read(spans, PAGE)

        assert reading.value(Field.REVISION) == "C1"
        assert reading.fields[Field.REVISION].how == "history"
        assert reading.needs_help()

    def test_an_odd_labelled_value_is_kept_but_not_trusted(self) -> None:
        spans = [span("DRAWING NO.", 620, 560, 2), span("SEE SCHEDULE", 620, 572, 4)]

        reading = read(spans, PAGE)

        assert reading.value(Field.SHEET_NUMBER) == "SEE SCHEDULE"
        assert reading.needs_help()

    def test_a_page_with_no_title_block_reads_nothing(self) -> None:
        reading = read([span("DN150 RISING MAIN", 100, 100)], PAGE)

        assert reading.value(Field.SHEET_NUMBER) is None
        assert reading.confidence == 0.0


class TestOcrText:
    def test_letter_for_digit_swaps_are_undone_in_codes(self) -> None:
        assert ocr_digits("FP-LO5-201") == "FP-L05-201"
        assert ocr_digits("RO4") == "R04"
        assert ocr_digits("P0I") == "P01"

    def test_letter_codes_are_left_alone(self) -> None:
        assert ocr_digits("FP-SCH-OO1") == "FP-SCH-001"
        assert ocr_digits("LO") == "LO"
        assert ocr_digits("C1") == "C1"

    def test_only_ocr_text_is_corrected(self) -> None:
        """A text layer says what it says; `FP-LO5` from a PDF is the consultant's typo."""
        spans = [span("DRAWING NO.", 620, 560, 2), span("FP-LO5-201", 620, 572, 4)]

        assert read(spans, PAGE).value(Field.SHEET_NUMBER) == "FP-LO5-201"

        ocr = [span("DRAWING NO.", 620, 560, 2, 0.9), span("FP-LO5-201", 620, 572, 4, 0.9)]
        corrected = read(ocr, PAGE)
        assert corrected.value(Field.SHEET_NUMBER) == "FP-L05-201"
        assert corrected.confidence_of(Field.SHEET_NUMBER) < 0.9, "OCR carries its doubt"

    @pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is not installed")
    def test_a_title_block_drawn_as_outlines_is_read_by_ocr(self, tmp_path: Path) -> None:
        document, truth = synthetic.general_arrangement()
        payload = synthetic.write_pdf(document, tmp_path / "outlined.pdf").read_bytes()
        assert pdf_text(payload, 0)["spans"] == [], "the fixture must have no text layer"

        corner = CANDIDATES[0]
        data = ocr_text(payload, "pdf", 0, [corner.x0, corner.y0, corner.x1, corner.y1])
        reading = reading_of(data)

        assert reading.value(Field.SHEET_NUMBER) == truth.sheet_number
        assert reading.value(Field.REVISION) == truth.revision
        assert data["mean_confidence"] > 0.8


class TestValues:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("12.06.2026", (2026, 6, 12)),
            ("12/06/2026", (2026, 6, 12)),
            ("2026-06-12", (2026, 6, 12)),
            ("12 JUN 2026", (2026, 6, 12)),
            ("12-Jun-2026", (2026, 6, 12)),
        ],
    )
    def test_dates_in_the_formats_consultants_use(
        self, text: str, expected: tuple[int, int, int]
    ) -> None:
        parsed = parse_date(text)
        assert parsed is not None
        assert (parsed.year, parsed.month, parsed.day) == expected

    def test_an_unreadable_date_is_none(self) -> None:
        assert parse_date("TBC") is None

    @pytest.mark.parametrize(
        ("number", "discipline"),
        [
            ("FP-L05-201", "fire protection"),
            ("FPS-001", "fire protection"),
            ("M-L02-001", "mechanical"),
            ("ACMV-L02-001", "mechanical"),
            ("XX-001", None),
        ],
    )
    def test_the_discipline_comes_from_the_prefix(
        self, number: str, discipline: str | None
    ) -> None:
        assert discipline_of(number) == discipline


class TestRememberedLayouts:
    def test_a_confirmed_layout_reads_the_next_sheet_by_position(self) -> None:
        first, _ = synthetic.general_arrangement(sheet_number="FP-L05-201", revision="R04")
        second, _ = synthetic.general_arrangement(sheet_number="FP-L06-201", revision="R02")
        first_data = dxf_text(synthetic.dxf_bytes(first), None)
        second_data = dxf_text(synthetic.dxf_bytes(second), None)
        first_page = Box(*first_data["page"])
        second_page = Box(*second_data["page"])
        first_spans = [Span(*item) for item in first_data["spans"]]
        second_spans = [Span(*item) for item in second_data["spans"]]

        layout = learn(read(first_spans, first_page), first_page)
        reading = read_with_layout(second_spans, second_page, layout)

        assert reading.value(Field.SHEET_NUMBER) == "FP-L06-201"
        assert reading.value(Field.REVISION) == "R02"
        assert reading.fields[Field.SHEET_NUMBER].how == "layout"
        assert reading.confidence >= 0.97

    def test_a_layout_survives_a_round_trip_through_json(self) -> None:
        document, _ = synthetic.general_arrangement()
        data = dxf_text(synthetic.dxf_bytes(document), None)
        page = Box(*data["page"])
        layout = learn(read([Span(*item) for item in data["spans"]], page), page)

        again = type(layout).from_json(layout.to_json())

        assert again.fields.keys() == layout.fields.keys()
        assert again.region.x0 == pytest.approx(layout.region.x0, abs=1e-4)

    def test_the_same_consultants_sheets_share_a_fingerprint(self) -> None:
        def print_of(document: Drawing) -> frozenset[tuple[str, int, int]]:
            data = dxf_text(synthetic.dxf_bytes(document), None)
            return fingerprint([Span(*item) for item in data["spans"]], Box(*data["page"]))

        one = print_of(synthetic.general_arrangement()[0])
        other = print_of(synthetic.not_to_scale_sheet()[0])
        different = frozenset({("sheet_number", 1, 1), ("revision", 2, 1)})

        assert similarity(one, other) > 0.8
        assert similarity(one, different) < 0.2
