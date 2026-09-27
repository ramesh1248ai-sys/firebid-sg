"""Reading real drawings: page sizes, content classes and the refusals (FR-DOC-01).

These run the actual parsers against actual generated files rather than fixtures checked in
as bytes, so a library upgrade that changes what PDFium or ezdxf reports fails here.

The parsers run in-process in these tests. What they do *inside* the sandbox is tested in
`tests/sandbox`; what they read is tested here.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import pytest

from firebid.evals.synthetic import dxf_bytes, general_arrangement, write_pdf, write_raster
from firebid.parsing.dxf import DxfUnreadable, inspect_dxf, render_layout
from firebid.parsing.pdf import (
    MIN_VECTOR_OBJECTS,
    PdfUnreadable,
    classify,
    inspect_pdf,
    render_page,
)

pytestmark = pytest.mark.req("FR-DOC-01")


@pytest.fixture(scope="module")
def drawing() -> Any:
    document, _ = general_arrangement(columns=6, rows=4)
    return document


@pytest.fixture(scope="module")
def vector_pdf(drawing: Any, tmp_path_factory: pytest.TempPathFactory) -> bytes:
    out = tmp_path_factory.mktemp("pdf")
    return write_pdf(drawing, out / "ga.pdf").read_bytes()


@pytest.fixture(scope="module")
def scanned_pdf(drawing: Any, tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """A drawing that went through a printer and a scanner: pixels, not geometry."""
    from PIL import Image

    out = tmp_path_factory.mktemp("scan")
    png = write_raster(drawing, out / "ga.png", dpi=72)
    target = out / "scan.pdf"
    with Image.open(png) as image:
        image.convert("RGB").save(target, "PDF", resolution=72.0)
    return target.read_bytes()


class TestReadingAPdf:
    def test_a_vector_drawing_has_one_page_with_a_real_size(self, vector_pdf: bytes) -> None:
        pages = inspect_pdf(vector_pdf)

        assert len(pages) == 1
        page = pages[0]
        assert page["width_mm"] > 100, "a drawing sheet is not a postcard"
        assert page["height_mm"] > 50
        assert page["index"] == 0

    def test_a_vector_drawing_is_classified_vector(self, vector_pdf: bytes) -> None:
        page = inspect_pdf(vector_pdf)[0]

        assert page["content_class"] == "vector"
        assert page["path_objects"] > MIN_VECTOR_OBJECTS
        assert page["image_objects"] == 0
        assert page["image_coverage"] == 0.0

    def test_a_scan_is_classified_raster(self, scanned_pdf: bytes) -> None:
        """The classifier's whole job: telling geometry from pixels before anything measures."""
        page = inspect_pdf(scanned_pdf)[0]

        assert page["content_class"] == "raster"
        assert page["image_objects"] >= 1
        assert page["image_coverage"] > 0.8

    def test_a_multi_page_pdf_reports_every_page(self, vector_pdf: bytes, tmp_path: Path) -> None:
        import pypdfium2 as pdfium

        merged = pdfium.PdfDocument.new()
        source = pdfium.PdfDocument(vector_pdf)
        for _ in range(3):
            merged.import_pages(source)
        target = tmp_path / "set.pdf"
        merged.save(target)

        pages = inspect_pdf(target.read_bytes())

        assert len(pages) == 3
        assert [page["index"] for page in pages] == [0, 1, 2]
        assert [page["label"] for page in pages] == ["page 1", "page 2", "page 3"]

    def test_a_damaged_pdf_is_refused_with_a_reason(self, vector_pdf: bytes) -> None:
        with pytest.raises(PdfUnreadable) as refused:
            inspect_pdf(vector_pdf[:200])

        assert "damaged or incomplete" in str(refused.value)

    def test_something_that_is_not_a_pdf_is_refused(self) -> None:
        with pytest.raises(PdfUnreadable):
            inspect_pdf(b"this is a text file, not a drawing")


class TestClassifying:
    """The rules, at their edges, without needing a file for each one."""

    def test_geometry_with_no_images_is_vector(self) -> None:
        assert classify(path_objects=400, text_objects=30, image_objects=0, image_coverage=0.0) == (
            "vector"
        )

    def test_a_full_page_image_is_raster(self) -> None:
        assert classify(path_objects=0, text_objects=0, image_objects=1, image_coverage=1.0) == (
            "raster"
        )

    def test_a_scan_with_a_stamped_title_block_is_still_raster(self) -> None:
        """Measuring it needs detection either way, so the vector garnish does not change it."""
        assert classify(path_objects=40, text_objects=20, image_objects=1, image_coverage=0.9) == (
            "raster"
        )

    def test_a_drawing_with_a_small_logo_is_vector(self) -> None:
        assert classify(
            path_objects=900, text_objects=80, image_objects=1, image_coverage=0.02
        ) == ("vector")

    def test_half_and_half_is_mixed(self) -> None:
        assert classify(path_objects=300, text_objects=40, image_objects=2, image_coverage=0.5) == (
            "mixed"
        )

    def test_nothing_on_the_page_is_empty(self) -> None:
        assert classify(path_objects=0, text_objects=0, image_objects=0, image_coverage=0.0) == (
            "empty"
        )

    def test_a_nearly_blank_page_is_empty_not_vector(self) -> None:
        """A separator sheet with a page number is not a drawing to measure."""
        assert classify(path_objects=1, text_objects=1, image_objects=0, image_coverage=0.0) == (
            "empty"
        )


class TestRenderingAPdf:
    def test_a_page_renders_at_the_size_asked_for(self, vector_pdf: bytes) -> None:
        rendered = render_page(vector_pdf, 0, 800, 600)

        assert (rendered["width"], rendered["height"]) == (800, 600)
        assert len(rendered["pixels"]) == 800 * 600 * 3

    def test_the_render_is_not_blank(self, vector_pdf: bytes) -> None:
        """A renderer that silently produces white paper would pass every other test here."""
        rendered = render_page(vector_pdf, 0, 400, 300)

        assert len(set(rendered["pixels"])) > 1, "the drawing did not reach the pixels"

    def test_a_page_that_does_not_exist_is_refused(self, vector_pdf: bytes) -> None:
        with pytest.raises(PdfUnreadable, match="no page 9"):
            render_page(vector_pdf, 8, 100, 100)


class TestReadingADxf:
    def test_a_drawing_reports_its_layout_and_size(self, drawing: Any) -> None:
        layouts = inspect_dxf(dxf_bytes(drawing))

        assert len(layouts) >= 1
        layout = layouts[0]
        assert layout["width_mm"] > 100
        assert layout["height_mm"] > 100
        assert layout["entity_count"] > 0
        assert layout["content_class"] == "vector", "a DXF is geometry by definition"

    def test_the_size_records_where_it_came_from(self, drawing: Any) -> None:
        """An estimator reading a suspect sheet size needs to know it was inferred."""
        layout = inspect_dxf(dxf_bytes(drawing))[0]

        assert layout["sized_from"] in {"page_setup", "extents", "fallback"}

    def test_paperspace_layouts_are_preferred_over_modelspace(self, drawing: Any) -> None:
        import ezdxf

        document = ezdxf.new("R2010", setup=True)
        document.modelspace().add_line((0, 0), (1000, 500))
        for name in ("FP-L05-201", "FP-L05-202"):
            layout = document.layouts.new(name)
            layout.page_setup(size=(841, 594), margins=(0, 0, 0, 0), units="mm")
            layout.add_line((0, 0), (800, 500))

        stream = io.StringIO()
        document.write(stream)
        layouts = inspect_dxf(stream.getvalue().encode())

        assert [layout["label"] for layout in layouts] == ["FP-L05-201", "FP-L05-202"]
        assert all(layout["space"] == "paper" for layout in layouts)
        assert layouts[0]["width_mm"] == 841.0
        assert layouts[0]["sized_from"] == "page_setup"

    def test_a_file_with_only_modelspace_becomes_one_sheet(self) -> None:
        """A consultant sending a bare model still expects to see their drawing."""
        import ezdxf

        document = ezdxf.new("R2010")
        document.modelspace().add_line((0, 0), (500, 300))
        stream = io.StringIO()
        document.write(stream)

        layouts = inspect_dxf(stream.getvalue().encode())

        assert len(layouts) == 1
        assert layouts[0]["space"] == "model"

    def test_a_damaged_dxf_is_refused_with_a_reason(self) -> None:
        with pytest.raises(DxfUnreadable) as refused:
            inspect_dxf(b"\x00\x01\x02 definitely not a dxf \xff")

        assert "damaged" in str(refused.value) or "not actually a DXF" in str(refused.value)


class TestRenderingADxf:
    def test_a_layout_renders_at_the_size_asked_for(self, drawing: Any) -> None:
        rendered = render_layout(dxf_bytes(drawing), 0, 640, 480)

        assert (rendered["width"], rendered["height"]) == (640, 480)
        assert len(rendered["pixels"]) == 640 * 480 * 3

    def test_the_render_is_not_blank(self, drawing: Any) -> None:
        rendered = render_layout(dxf_bytes(drawing), 0, 320, 240)

        assert len(set(rendered["pixels"])) > 1

    def test_a_layout_that_does_not_exist_is_refused(self, drawing: Any) -> None:
        with pytest.raises(DxfUnreadable, match="no layout"):
            render_layout(dxf_bytes(drawing), 7, 100, 100)


def test_an_office_file_is_not_mistaken_for_a_drawing() -> None:
    """The parsers are reached by kind, but a wrong kind must fail loudly, not silently."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", b"<xml/>")

    with pytest.raises(PdfUnreadable):
        inspect_pdf(buffer.getvalue())
