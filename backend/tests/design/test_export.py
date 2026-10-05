"""A proposed layout over its tender drawing, as a PDF and a DXF (FR-DSN-05)."""

from __future__ import annotations

import io

import ezdxf
import pdfplumber
import pytest

from firebid.design import export
from tests.design.plans import REGION, building

pytestmark = pytest.mark.req("FR-DSN-05")

HEADS = [
    export.Head(80.0, 100.0, "sprinkler_pendent"),
    export.Head(110.0, 100.0, "sprinkler_pendent"),
]
PIPES = [
    export.Pipe([(80.0, 100.0), (110.0, 100.0)], 25, "range"),
    export.Pipe([(80.0, 100.0), (80.0, 160.0)], 32, "feed"),
]


def sheet(**changes: object) -> export.Sheet:
    plan = building()
    plan.pipe(60.0, 160.0, 340.0, 160.0)
    found = export.Sheet(
        number="FP-L10-01",
        page=REGION,
        table=plan.table(),
        heads=list(HEADS),
        pipes=list(PIPES),
        notes=["Criterion: ordinary hazard: at most 12 m2 a head.", "2 proposed heads."],
    )
    for name, value in changes.items():
        setattr(found, name, value)
    return found


def test_the_pdf_is_one_page_the_size_of_the_sheet_and_is_stamped() -> None:
    content = export.as_pdf(sheet())

    assert content.startswith(b"%PDF")
    with pdfplumber.open(io.BytesIO(content)) as document:
        (page,) = document.pages
        words = " ".join((page.extract_text() or "").split())
        # 400 mm x 300 mm, in points.
        assert (round(page.width), round(page.height)) == (
            round(400 / 25.4 * 72),
            round(300 / 25.4 * 72),
        )
    assert export.STAMP.upper() in words
    assert "Proposed sprinkler layout on FP-L10-01: an estimating aid, not a design." in words
    assert "Criterion: ordinary hazard: at most 12 m2 a head." in words
    # The tender drawing's own words are on it, and the proposed pipe's sizes.
    assert "WARD A" in words and "STORE" in words
    assert "DN25" in words and "DN32" in words


def test_the_dxf_keeps_the_tender_drawing_and_the_proposal_on_layers_of_their_own() -> None:
    content = export.as_dxf(sheet(scope=[(0.0, 0.0), (200.0, 0.0), (200.0, 300.0), (0.0, 300.0)]))

    document = ezdxf.read(io.StringIO(content.decode("utf-8")))
    space = document.modelspace()

    def on(layer: str, kind: str) -> list[ezdxf.entities.DXFGraphic]:
        return [e for e in space if e.dxf.layer == layer and e.dxftype() == kind]

    heads = on(export.LAYER_HEADS, "CIRCLE")
    assert len(heads) == 2
    # Sheet millimetres with y up: a head 100 mm from the top of a 300 mm sheet is at y = 200.
    assert sorted((round(h.dxf.center.x), round(h.dxf.center.y)) for h in heads) == [
        (80, 200),
        (110, 200),
    ]
    assert len(on(export.LAYER_PIPE, "POLYLINE")) == 2
    assert {t.dxf.text for t in on(export.LAYER_PIPE, "TEXT")} == {"DN25 range", "DN32 feed"}
    assert len(on(export.LAYER_SCOPE, "POLYLINE")) == 1
    assert len(on(export.LAYER_TENDER, "LINE")) > 20
    assert {"WARD A", "STORE"} <= {t.dxf.text for t in on(export.LAYER_TENDER, "TEXT")}
    stamped = [t.dxf.text for t in on(export.LAYER_STAMP, "TEXT")]
    assert stamped.count(export.STAMP.upper()) == 2  # across the sheet, and heading the note
    assert "2 proposed heads." in stamped


def test_the_same_layout_gives_the_same_dxf() -> None:
    assert export.as_dxf(sheet()) != b""
    first = ezdxf.read(io.StringIO(export.as_dxf(sheet()).decode("utf-8")))
    second = ezdxf.read(io.StringIO(export.as_dxf(sheet()).decode("utf-8")))

    def shape(document: ezdxf.document.Drawing) -> list[tuple[str, str]]:
        return [(e.dxftype(), e.dxf.layer) for e in document.modelspace()]

    assert shape(first) == shape(second)


def test_only_pdf_and_dxf_are_offered() -> None:
    assert set(export.FORMATS) == {"pdf", "dxf"}
    with pytest.raises(ValueError, match="pdf or dxf"):
        export.render(sheet(), "dwg")


def test_words_written_up_the_sheet_stay_written_up_the_sheet() -> None:
    # Found on a real sheet: a note written along a match line came out upside down.
    plan = building()
    plan.builder.text(
        "FOR CONTINUATION",
        (195.0, 120.0, 201.0, 240.0),
        plan.builder.group(),
        height=6.0,
        rotation=90.0,
    )
    content = export.as_dxf(sheet(table=plan.table()))

    document = ezdxf.read(io.StringIO(content.decode("utf-8")))
    (note,) = [
        e
        for e in document.modelspace()
        if e.dxftype() == "TEXT" and e.dxf.text == "FOR CONTINUATION"
    ]
    # It starts at the bottom of its box (y = 240 of 300, so 60 up) and runs up the sheet,
    # its feet to the right (x = 201).
    assert note.dxf.rotation == 90.0
    assert (round(note.dxf.insert.x), round(note.dxf.insert.y)) == (201, 60)
