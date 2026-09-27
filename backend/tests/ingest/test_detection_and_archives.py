"""Identifying files by content, and opening archives that may be hostile (FR-DOC-01, NFR-06)."""

from __future__ import annotations

import io
import zipfile

import pytest

from firebid.ingest.archives import (
    MAX_DEPTH,
    MAX_ENTRIES,
    ArchiveRefused,
    expand,
)
from firebid.ingest.detection import FileKind, detect

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
DXF = b"  0\r\nSECTION\r\n  2\r\nHEADER\r\n  0\r\nENDSEC\r\n  0\r\nEOF\r\n"
DWG = b"AC1027" + b"\x00" * 64


def zip_of(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def ooxml(marker: str) -> bytes:
    return zip_of({"[Content_Types].xml": b"<Types/>", marker: b"<xml/>"})


@pytest.mark.req("FR-DOC-01")
class TestDetection:
    def test_a_pdf(self) -> None:
        assert detect(PDF).kind is FileKind.PDF

    def test_a_dxf(self) -> None:
        assert detect(DXF).kind is FileKind.DXF

    def test_a_dwg_reports_its_format_version(self) -> None:
        result = detect(DWG)
        assert result.kind is FileKind.DWG
        assert "AC1027" in result.detail

    def test_a_spreadsheet_and_a_document_are_told_apart(self) -> None:
        assert detect(ooxml("xl/workbook.xml")).kind is FileKind.XLSX
        assert detect(ooxml("word/document.xml")).kind is FileKind.DOCX

    def test_a_plain_archive_is_not_mistaken_for_a_document(self) -> None:
        assert detect(zip_of({"a.pdf": PDF})).kind is FileKind.ZIP

    def test_the_extension_does_not_decide(self) -> None:
        """A consultant's `.pdf` that is really a DXF must be read as a DXF."""
        assert detect(DXF, "GA-PLAN.pdf").kind is FileKind.DXF
        assert detect(PDF, "drawing.dwg").kind is FileKind.PDF

    def test_an_empty_file_says_so(self) -> None:
        result = detect(b"")
        assert result.kind is FileKind.UNKNOWN
        assert "empty" in result.detail

    def test_an_unknown_file_describes_what_it_saw(self) -> None:
        """A rejection an estimator can act on beats 'unsupported'."""
        result = detect(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 32, "viewer.bin")
        assert result.kind is FileKind.UNKNOWN
        assert "ELF" in result.detail or "\\x7f" in result.detail

    def test_a_truncated_zip_is_described_as_damaged(self) -> None:
        broken = zip_of({"a.pdf": PDF})[:40]
        result = detect(broken)
        assert result.kind is FileKind.UNKNOWN
        assert "damaged" in result.detail or "truncated" in result.detail

    def test_a_legacy_workbook_is_recognised(self) -> None:
        ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 200 + b"Book" + b"\x00" * 100
        assert detect(ole, "rates.xls").kind is FileKind.XLS


@pytest.mark.req("NFR-06")
class TestArchiveLimits:
    def test_a_normal_set_expands(self) -> None:
        entries = list(expand(zip_of({"A-01.pdf": PDF, "A-02.pdf": PDF + b"x"})))
        assert {entry.name for entry in entries} == {"A-01.pdf", "A-02.pdf"}

    def test_a_zip_bomb_is_refused_before_it_is_decompressed(self) -> None:
        """Highly compressible zeroes: the classic shape, refused on the declared ratio."""
        bomb = zip_of({"bomb.bin": b"\x00" * (8 * 1024 * 1024)})
        with pytest.raises(ArchiveRefused, match="compression bomb"):
            list(expand(bomb))

    def test_a_small_file_that_compresses_well_is_not_a_bomb(self) -> None:
        """A text schedule compresses hugely and is perfectly normal."""
        entries = list(expand(zip_of({"notes.txt": b"a" * 4096})))
        assert len(entries) == 1

    def test_too_many_entries_is_refused(self) -> None:
        many = zip_of({f"f{index}.txt": b"x" for index in range(MAX_ENTRIES + 1)})
        with pytest.raises(ArchiveRefused, match="entries"):
            list(expand(many))

    def test_nesting_beyond_the_limit_is_refused(self) -> None:
        payload = zip_of({"leaf.pdf": PDF})
        for depth in range(MAX_DEPTH + 2):
            payload = zip_of({f"level{depth}.zip": payload})
        with pytest.raises(ArchiveRefused, match="nested"):
            list(expand(payload))

    def test_nesting_within_the_limit_is_followed(self) -> None:
        inner = zip_of({"A-01.pdf": PDF})
        outer = zip_of({"drawings.zip": inner})
        entries = list(expand(outer))
        assert [entry.name for entry in entries] == ["A-01.pdf"]

    def test_an_ooxml_file_inside_an_archive_is_kept_whole(self) -> None:
        """A .xlsx is a ZIP; expanding it would turn one BOQ into a pile of XML."""
        entries = list(expand(zip_of({"boq.xlsx": ooxml("xl/workbook.xml")})))
        assert [entry.name for entry in entries] == ["boq.xlsx"]

    def test_a_traversing_name_is_neutered_not_obeyed(self) -> None:
        entries = list(expand(zip_of({"../../etc/passwd": b"root:x:0:0"})))
        assert entries[0].name == "etc/passwd", "the traversal should be stripped"
        assert ".." not in entries[0].name

    def test_an_absolute_name_is_made_relative(self) -> None:
        entries = list(expand(zip_of({"/tmp/evil.pdf": PDF})))  # noqa: S108 - the point
        assert not entries[0].name.startswith("/")

    def test_a_damaged_archive_says_so(self) -> None:
        with pytest.raises(ArchiveRefused, match="not a readable archive"):
            list(expand(b"PK\x03\x04 and then nonsense"))
