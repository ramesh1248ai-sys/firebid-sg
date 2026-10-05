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
    DRAWING_NUMBER,
    Box,
    Field,
    Span,
    TitleBlockReading,
    discipline_of,
    fingerprint,
    joined,
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


class TestTheRevisionHistoryAsEvidence:
    """The REV cell and the history table are two readings of the same label."""

    def history_block(self, rev_cell: str, confidence: float = 1.0) -> list[Span]:
        return [
            span("REV", 700, 470, 2, confidence),
            span("DESCRIPTION", 740, 470, 2, confidence),
            span("C1", 700, 475, 2.5, confidence),
            span("TENDER ISSUE", 740, 475, 2.5, confidence),
            span("DRAWING NO.", 620, 560, 2, confidence),
            span("FP-L03-357", 620, 572, 4, confidence),
            span("REV", 720, 560, 2, confidence),
            span(rev_cell, 720, 572, 4, confidence),
        ]

    def test_an_ocr_misreading_is_undone_by_the_history(self) -> None:
        reading = read(self.history_block("CL", confidence=0.9), PAGE)

        assert reading.value(Field.REVISION) == "C1"
        assert reading.fields[Field.REVISION].how == "label+history"

    def test_the_history_is_cleaned_like_the_cell(self) -> None:
        """OCR reads R04 as RO4 in both places; the fix must not turn the right one wrong."""
        spans = self.history_block("RO4", confidence=0.9)
        spans[2] = span("RO4", 700, 475, 2.5, 0.9)

        reading = read(spans, PAGE)

        assert reading.value(Field.REVISION) == "R04"
        assert reading.history == ("R04",)
        assert not reading.fields[Field.REVISION].how.endswith("history")

    def test_ocr_punctuation_is_dropped(self) -> None:
        reading = read(self.history_block("C1,", confidence=0.9), PAGE)

        assert reading.value(Field.REVISION) == "C1"

    def test_a_revision_its_own_history_lacks_is_a_question(self) -> None:
        """A text layer says what it says; C2 missing from the history is flagged, not changed."""
        reading = read(self.history_block("C2"), PAGE)

        assert reading.value(Field.REVISION) == "C2"
        assert reading.needs_help()

    def test_an_ocr_value_with_no_matching_variant_is_left_and_flagged(self) -> None:
        reading = read(self.history_block("T4", confidence=0.9), PAGE)

        assert reading.value(Field.REVISION) == "T4"
        assert reading.needs_help()


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


class TestAProjectNumberedTitleBlock:
    """A layout seen on a real Singapore tender set: the drawing number is the project number,
    a bracketed building code and a sheet code (`7310(ABC)-F/2C`), which the PDF writer splits
    into two runs of text; the revision cell holds a dash (first issue); the date is a month
    and year set in across its cell, with a (c) mark below it on the copyright line; and a
    note elsewhere refers to another sheet by its number."""

    def spans(self, history_row: bool = False) -> list[Span]:
        spans = [
            span("Drawing Title:", 720.8, 507.4, 3.3),
            span("MAIN PIPING LAYOUT", 720.8, 516.4, 3.7),
            span("BASEMENT 2M PLAN - ZONE 1", 720.8, 522.3, 3.7),
            span("Drawing No.:", 720.8, 537.4, 3.3),
            span("Revision:", 800.7, 537.4, 2.6),
            # One number, two runs: `7310(ABC)-F/` then `2C`, 0.2 mm apart.
            Span("7310(ABC)-F/", 743.2, 541.8, 786.1, 548.4),
            Span("2C", 786.3, 541.8, 794.6, 547.0),
            Span("-", 808.2, 545.4, 809.6, 545.8),
            span("Date:", 720.8, 552.4, 2.6),
            span("Scale:", 760.7, 552.4, 2.6),
            span("Size:", 800.8, 552.4, 2.6),
            Span("JUL 2026", 741.0, 556.3, 755.6, 558.9),
            Span("AS SHOWN", 776.9, 556.3, 795.7, 558.9),
            span("Drawn:", 720.8, 562.4, 2.6),
            Span("A1", 806.4, 564.4, 811.4, 568.0),
            Span("C", 721.5, 572.8, 723.0, 574.6),
            Span("COPYRIGHT 2026 EXAMPLE CONSULTING ENGINEERS", 725.3, 572.8, 794.8, 574.6),
            # A note on the plan naming another sheet, in small text.
            span("7310(ABC)-F/1 FOR GENERAL NOTES", 700.0, 81.0, 2.0),
        ]
        if history_row:
            spans += [
                span("Rev", 720.0, 197.4, 2.0),
                span("Date", 730.9, 197.4, 2.0),
                span("Description", 771.9, 197.1, 2.0),
                span("A", 721.4, 203.4, 2.0),
                span("26.09.26", 728.9, 203.4, 2.0),
                span("HEADS AND BRANCH PIPEWORK ADDED", 745.1, 203.8, 2.0),
            ]
        return spans

    def test_the_split_number_the_dash_and_the_month_are_read(self) -> None:
        reading = read(self.spans(), PAGE)

        assert reading.value(Field.SHEET_NUMBER) == "7310(ABC)-F/2C"
        assert reading.value(Field.REVISION) == "-"
        assert reading.value(Field.REVISION_DATE) == "JUL 2026"
        assert reading.value(Field.TITLE) == "MAIN PIPING LAYOUT BASEMENT 2M PLAN - ZONE 1"
        assert not reading.needs_help(DEFAULT_THRESHOLD)

    def test_a_history_naming_a_revision_the_rev_cell_does_not_is_a_question(self) -> None:
        # A mark-up added "Rev A" to the history and left the REV cell at "-".
        reading = read(self.spans(history_row=True), PAGE)

        assert reading.value(Field.SHEET_NUMBER) == "7310(ABC)-F/2C"
        assert reading.fields[Field.REVISION].confidence < DEFAULT_THRESHOLD

    def test_a_doubtful_revision_does_not_send_a_read_number_to_ocr(self) -> None:
        """On the real set, OCR of such a sheet took the paper size (A1) for the revision and,
        scoring higher, replaced the text layer's right reading. The doubt goes to a person."""
        from firebid.services.title_blocks import _number_from_text

        doubtful = read(self.spans(history_row=True), PAGE)
        unnumbered = read([s for s in self.spans() if not s.text.startswith(("7310", "2C"))], PAGE)

        assert doubtful.needs_help(DEFAULT_THRESHOLD)
        assert _number_from_text(doubtful) is True
        assert _number_from_text(unnumbered) is False

    @pytest.mark.parametrize(
        ("text", "number"),
        [
            ("7310(ABC)-F/2C", True),
            ("7310-F-001", True),
            ("FP-L05-201", True),
            ("2026-09-26", False),
            ("26/09/2026", False),
            ("26.09.26", False),
        ],
    )
    def test_project_numbered_drawings_are_numbers_and_dates_are_not(
        self, text: str, number: bool
    ) -> None:
        assert bool(DRAWING_NUMBER.fullmatch(text)) is number

    def test_runs_are_joined_only_across_a_hairline_gap(self) -> None:
        together = joined([Span("F/", 10.0, 0.0, 14.0, 5.0), Span("2C", 14.2, 0.3, 19.0, 4.8)])
        apart = joined([Span("SCALE", 10.0, 0.0, 22.0, 5.0), Span("1:100", 25.0, 0.0, 36.0, 5.0)])
        another_line = joined([Span("F/", 10.0, 0.0, 14.0, 5.0), Span("2C", 14.2, 9.0, 19.0, 14.0)])

        assert [s.text for s in together] == ["F/2C"]
        assert [s.text for s in apart] == ["SCALE", "1:100"]
        assert sorted(s.text for s in another_line) == ["2C", "F/"]

    def test_a_month_and_year_is_the_first_of_the_month(self) -> None:
        parsed = parse_date("JUL 2026")
        assert parsed is not None and (parsed.year, parsed.month, parsed.day) == (2026, 7, 1)


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


class TestANamingStandardNumber:
    """Found on a real tender: a title block down the right edge of an A0 sheet whose
    drawing number is built to a naming standard, seven parts long, in a cell of its own
    under SHEET NUMBER, with DATE and SCALE cells above it."""

    A0 = Box(0, 0, 1189, 841)
    NUMBER = "60743399_ACM_TN_TO_D_MFP_A03-10-01"

    def spans(self) -> list[Span]:
        return [
            Span("DRAWING TITLE:", 1072, 745, 1088, 747),
            Span("FIRE PROTECTION ENLARGEMENT LAYOUT", 1072, 756, 1149, 760),
            Span("10TH STOREY - SHEET 1", 1072, 762, 1112, 766),
            Span("DRAWN BY:", 1072, 777, 1084, 779),
            Span("SCALE:", 1138, 777, 1145, 779),
            Span("As indicated", 1155, 777, 1169, 779),
            Span("DATE:", 1072, 784, 1077, 786),
            Span("JUNE 2026", 1108, 784, 1119, 786),
            Span("PROJ NO:", 1122, 784, 1132, 786),
            Span("60743399", 1134, 784, 1144, 786),
            Span("SHEET NUMBER:", 1071, 798, 1088, 800),
            Span("REVISION:", 1147, 798, 1157, 800),
            Span(self.NUMBER, 1079, 810, 1135, 813),
            Span("00", 1156, 810, 1159, 813),
            # Elsewhere in the block: abbreviations that have a short drawing number's shape,
            # and the consultant's telephone number.
            Span("SPR-PA", 1072, 228, 1080, 230),
            Span("T/B", 1072, 215, 1076, 217),
            Span("Tel: 65 - 63363900", 1071, 657, 1100, 660),
            Span("65-63363900", 1101, 657, 1120, 660),
        ]

    def test_the_number_under_its_label_is_read_whole_and_trusted(self) -> None:
        reading = read(self.spans(), self.A0)

        assert reading.value(Field.SHEET_NUMBER) == self.NUMBER
        assert reading.fields[Field.SHEET_NUMBER].how == "label"
        assert reading.value(Field.REVISION) == "00"
        assert not reading.needs_help()

    def test_the_date_label_above_does_not_take_the_number_for_its_date(self) -> None:
        reading = read(self.spans(), self.A0)

        assert reading.value(Field.REVISION_DATE) != self.NUMBER
        assert reading.value(Field.SCALE) == "AS INDICATED"

    def test_a_telephone_number_is_not_a_drawing_number(self) -> None:
        from firebid.drawings.title_block import DRAWING_NUMBER

        assert DRAWING_NUMBER.fullmatch(self.NUMBER)
        assert DRAWING_NUMBER.fullmatch("6405(HFC)-F/1B")
        assert not DRAWING_NUMBER.fullmatch("65-63363900")
        assert not DRAWING_NUMBER.fullmatch("2026-09-26")


def offset_page_pdf() -> bytes:
    """A one-page PDF whose origin is the middle of the sheet, as some CAD exports write it:
    600 x 400 points, with "SHEET NUMBER" in its bottom-right quarter."""
    stream = b"BT /F1 12 Tf 150 -150 Td (SHEET NUMBER) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [-300 -200 300 200] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode()
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(xref).encode()
        + b"\n%%EOF\n"
    )
    return bytes(out)


def test_text_is_placed_on_the_sheet_when_the_page_s_origin_is_not_its_corner() -> None:
    # Found on a real tender: every word was read half a sheet away from where it is drawn,
    # so the title block was looked for where it was not and no sheet was identified.
    data = pdf_text(offset_page_pdf(), 0)

    _, _, width, height = data["page"]
    (found,) = data["spans"]
    words, x0, y0, x1, y1, _ = found
    assert words == "SHEET NUMBER"
    assert (round(width), round(height)) == (212, 141)  # 600 x 400 points, in millimetres
    # In the bottom-right quarter, on the sheet: 450 of 600 points across, 350 of 400 down.
    assert x0 == pytest.approx(450 / 72 * 25.4, abs=1.0)
    assert 0.8 * height < y0 < y1 < 0.9 * height
    assert width / 2 < x0 < x1 < width
