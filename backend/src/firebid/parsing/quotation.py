"""A supplier's quotation file as lines of cells: the sandbox's part of reading one (P2-04).

A quotation arrives as a PDF, a workbook, or an email with either attached. This opens the
file (in the sandbox: it came from outside the company) and returns its content as plain
data: lines, each a list of cells, in reading order. What the cells mean is decided
afterwards by `pricing.quotation`, outside the sandbox, from this data alone.

* **Workbook:** each sheet's rows, the cells that hold something.
* **PDF:** each page's words grouped into lines by their baseline, and into cells where the
  gap between two words is wider than a space: a table's columns stay apart.
* **Email (.eml):** its From, Subject and Date as lines, then its body, then each
  attachment that is a PDF or a workbook, read the same way. An Outlook `.msg` is not read:
  save it as `.eml`.

Every line says where it came from (`part`, `page` or `sheet`, and `row`), which is the
source reference a person checks an extracted field against.
"""

from __future__ import annotations

import email
import email.policy
import io
from typing import Any

# Two words further apart than this, in points, are in different cells.
CELL_GAP_PT = 9.0
KINDS = ("pdf", "xlsx", "eml")
MOST_LINES = 5_000


def kind_of(payload: bytes, filename: str) -> str | None:
    """`pdf`, `xlsx` or `eml`, by content; None for anything else (an Outlook .msg too)."""
    name = filename.lower()
    if payload.startswith(b"%PDF-"):
        return "pdf"
    if (payload.startswith(b"PK") and b"xl/" in payload[:4096]) or (
        payload.startswith(b"PK") and name.endswith(".xlsx")
    ):
        return "xlsx"
    head = payload[:4096].decode("utf-8", errors="ignore").lower()
    if name.endswith(".eml") or (
        "\nfrom:" in "\n" + head and ("\nsubject:" in "\n" + head or "mime-version:" in head)
    ):
        return "eml" if not payload.startswith(b"\xd0\xcf\x11\xe0") else None
    return None


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if hasattr(value, "isoformat"):
        return str(value.isoformat())[:10]
    return str(value).strip()


def _workbook(payload: bytes, part: str) -> list[dict[str, Any]]:
    from openpyxl import load_workbook

    lines: list[dict[str, Any]] = []
    book = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    for sheet in book.worksheets:
        for index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            cells = [_cell(value) for value in row]
            while cells and not cells[-1]:
                cells.pop()
            if any(cells):
                lines.append({"part": part, "sheet": sheet.title, "row": index, "cells": cells})
            if len(lines) >= MOST_LINES:
                return lines
    return lines


def _pdf(payload: bytes, part: str) -> list[dict[str, Any]]:
    import pdfplumber

    lines: list[dict[str, Any]] = []
    with pdfplumber.open(io.BytesIO(payload)) as document:
        for number, page in enumerate(document.pages, start=1):
            words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
            rows: dict[int, list[dict[str, Any]]] = {}
            for word in words:
                rows.setdefault(round(float(word["top"]) / 3), []).append(word)
            for row, key in enumerate(sorted(rows), start=1):
                ordered = sorted(rows[key], key=lambda w: float(w["x0"]))
                cells: list[str] = []
                last_end: float | None = None
                for word in ordered:
                    if last_end is not None and float(word["x0"]) - last_end <= CELL_GAP_PT:
                        cells[-1] = f"{cells[-1]} {word['text']}"
                    else:
                        cells.append(str(word["text"]))
                    last_end = float(word["x1"])
                lines.append({"part": part, "page": number, "row": row, "cells": cells})
                if len(lines) >= MOST_LINES:
                    return lines
    return lines


def _text(text: str, part: str) -> list[dict[str, Any]]:
    lines = []
    for row, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        # A table typed into an email: tabs, pipes, or runs of spaces between its cells.
        raw = line.replace("|", "\t")
        cells = [cell.strip() for cell in raw.split("\t")] if "\t" in raw else None
        if cells is None:
            import re

            cells = [cell.strip() for cell in re.split(r"\s{3,}", line.strip())]
        lines.append({"part": part, "row": row, "cells": [cell for cell in cells if cell]})
    return lines


def _email(payload: bytes) -> list[dict[str, Any]]:
    message = email.message_from_bytes(payload, policy=email.policy.default)
    lines: list[dict[str, Any]] = []
    for row, header in enumerate(("From", "Subject", "Date"), start=1):
        if message[header]:
            lines.append(
                {"part": "headers", "row": row, "cells": [f"{header}:", str(message[header])]}
            )
    body = message.get_body(preferencelist=("plain",))
    if body is not None:
        lines.extend(_text(str(body.get_content()), "body"))
    for attachment in message.iter_attachments():
        name = attachment.get_filename() or "attachment"
        content = attachment.get_payload(decode=True)
        if not isinstance(content, bytes):
            continue
        kind = kind_of(content, name)
        if kind == "pdf":
            lines.extend(_pdf(content, name))
        elif kind == "xlsx":
            lines.extend(_workbook(content, name))
    return lines[:MOST_LINES]


def read(payload: bytes, kind: str) -> dict[str, Any]:
    """The file as lines of cells. Sandbox entry: its argument and result are plain data."""
    if kind == "pdf":
        lines = _pdf(payload, "document")
    elif kind == "xlsx":
        lines = _workbook(payload, "document")
    elif kind == "eml":
        lines = _email(payload)
    else:
        raise ValueError(f"a quotation is a PDF, a workbook or an .eml email, not {kind!r}")
    return {"kind": kind, "lines": lines}
