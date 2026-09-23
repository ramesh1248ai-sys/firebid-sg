"""What a file actually is, decided by its content and not its name.

A tender set arrives with `.pdf` files that are really scans in a ZIP, `.dwg` files that are
DXF, and the occasional `.PDF` that is a Word document somebody renamed. Trusting the
extension is how a parser gets handed something it cannot read, or worse, something it can.

The signature set is deliberately small: the formats Phase 1 actually accepts, plus enough of
the common ones to give a useful rejection message. Anything unrecognised is rejected with its
first bytes described, never guessed at.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO


class FileKind(StrEnum):
    """What the ingestion pipeline does with a file."""

    PDF = "pdf"
    DXF = "dxf"
    DWG = "dwg"
    XLSX = "xlsx"
    DOCX = "docx"
    XLS = "xls"  # legacy, converted
    DOC = "doc"  # legacy, converted
    ZIP = "zip"
    IMAGE = "image"
    UNKNOWN = "unknown"


# Kinds Phase 1 can open. DWG is knowingly absent: see `dwg_supported`.
PARSEABLE = frozenset(
    {FileKind.PDF, FileKind.DXF, FileKind.XLSX, FileKind.DOCX, FileKind.XLS, FileKind.DOC}
)
LEGACY_OFFICE = frozenset({FileKind.XLS, FileKind.DOC})


@dataclass(frozen=True)
class Detected:
    kind: FileKind
    media_type: str
    # Why, when the answer is "unknown" — so a person reading the rejection learns something.
    detail: str = ""


# Magic numbers, longest first so a more specific match wins.
_SIGNATURES: tuple[tuple[bytes, FileKind, str], ...] = (
    (b"%PDF-", FileKind.PDF, "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", FileKind.IMAGE, "image/png"),
    (b"\xff\xd8\xff", FileKind.IMAGE, "image/jpeg"),
    (b"II*\x00", FileKind.IMAGE, "image/tiff"),
    (b"MM\x00*", FileKind.IMAGE, "image/tiff"),
    # Compound File Binary: legacy .doc and .xls both use it, told apart below.
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", FileKind.UNKNOWN, ""),
)

# DWG files start "AC" followed by a four-digit version, e.g. AC1027 for AutoCAD 2013.
_DWG_PREFIX = b"AC10"

# OOXML is a ZIP; the part names say which application wrote it.
_OOXML_MARKERS: tuple[tuple[str, FileKind, str], ...] = (
    (
        "xl/workbook.xml",
        FileKind.XLSX,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    (
        "word/document.xml",
        FileKind.DOCX,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
)


def detect(payload: bytes, filename: str = "") -> Detected:
    """Identify a file from its first bytes, using the name only to break a genuine tie."""
    if not payload:
        return Detected(FileKind.UNKNOWN, "application/octet-stream", "the file is empty")

    head = payload[:512]

    if head.startswith(b"%PDF-"):
        return Detected(FileKind.PDF, "application/pdf")
    if head.startswith(_DWG_PREFIX):
        version = head[:6].decode("ascii", "replace")
        return Detected(FileKind.DWG, "image/vnd.dwg", f"AutoCAD drawing, format {version}")
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return _compound_file(payload, filename)
    if head.startswith(b"PK\x03\x04"):
        return _zip_like(payload)

    for signature, kind, media_type in _SIGNATURES:
        if kind is not FileKind.UNKNOWN and head.startswith(signature):
            return Detected(kind, media_type)

    if _looks_like_dxf(head):
        return Detected(FileKind.DXF, "image/vnd.dxf")

    return Detected(
        FileKind.UNKNOWN,
        "application/octet-stream",
        f"unrecognised content, starting {head[:8]!r}",
    )


def _looks_like_dxf(head: bytes) -> bool:
    """DXF is plain text: a group code 0, then SECTION. Whitespace and line endings vary."""
    try:
        text = head.decode("utf-8", "ignore")
    except UnicodeDecodeError:  # pragma: no cover - decode with "ignore" does not raise
        return False
    stripped = text.lstrip("﻿ \t\r\n")
    if stripped.startswith("0") and "SECTION" in text[:256].upper():
        return True
    # Binary DXF has its own sentinel.
    return head.startswith(b"AutoCAD Binary DXF")


def _compound_file(payload: bytes, filename: str) -> Detected:
    """Legacy .doc and .xls share a container, so look for each application's stream name."""
    window = payload[:8192]
    # Stream names are UTF-16LE inside the directory entries.
    if b"W\x00o\x00r\x00k\x00b\x00o\x00o\x00k\x00" in window or b"Book" in window:
        return Detected(FileKind.XLS, "application/vnd.ms-excel")
    if b"W\x00o\x00r\x00d\x00D\x00o\x00c\x00u\x00m\x00e\x00n\x00t\x00" in window:
        return Detected(FileKind.DOC, "application/msword")

    # Some writers put the marker beyond our window; fall back to the name, which is the one
    # place a filename is better than nothing.
    lowered = filename.lower()
    if lowered.endswith(".xls"):
        return Detected(FileKind.XLS, "application/vnd.ms-excel", "identified by extension")
    if lowered.endswith(".doc"):
        return Detected(FileKind.DOC, "application/msword", "identified by extension")
    return Detected(
        FileKind.UNKNOWN,
        "application/x-ole-storage",
        "a legacy Office container, but not Word or Excel",
    )


def _zip_like(payload: bytes) -> Detected:
    """A ZIP may be an archive, or an OOXML document wearing one."""
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            names = set(archive.namelist()[:200])
    except zipfile.BadZipFile:
        # Truncated uploads land here; the reason matters to whoever re-sends the file.
        return Detected(FileKind.UNKNOWN, "application/zip", "a damaged or truncated ZIP")

    for marker, kind, media_type in _OOXML_MARKERS:
        if marker in names:
            return Detected(kind, media_type)
    return Detected(FileKind.ZIP, "application/zip")


def dwg_supported() -> bool:
    """Whether DWG can be converted yet.

    False until ADR-003 records a converter licence. DWG files are then recorded as awaiting
    conversion rather than rejected, so they read as blocked on a decision rather than broken.
    """
    from firebid.settings import get_settings

    return bool(get_settings().dwg_converter_command)
