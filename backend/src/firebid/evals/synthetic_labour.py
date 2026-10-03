"""A synthetic productivity list, with entries for the synthetic tender's bill (P2-05).

Every figure is made up: it proves the arithmetic and the provenance, not a productivity.
"""

from __future__ import annotations

import io
from typing import Any

HEADER = (
    "Type",
    "DN",
    "Joining",
    "Description",
    "Unit",
    "Man-hours per unit",
    "Trade",
    "Source type",
    "Source reference",
)

# (type, dn, joining, description, unit, man-hours per unit, trade, source type, reference)
ENTRIES: tuple[tuple[Any, ...], ...] = (
    (
        "pipe",
        "",
        "",
        "Steel pipe, any size",
        "m",
        0.35,
        "pipefitter",
        "company standard",
        "PS-2026",
    ),
    ("pipe", 50, "", "Steel pipe DN50", "m", 0.30, "pipefitter", "company standard", "PS-2026"),
    ("pipe", 100, "", "Steel pipe DN100", "m", 0.45, "pipefitter", "company standard", "PS-2026"),
    (
        "pipe",
        150,
        "",
        "Steel pipe DN150",
        "m",
        0.62,
        "pipefitter",
        "historical project",
        "Tampines Hub (2025)",
    ),
    (
        "sprinkler_pendent",
        "",
        "",
        "Pendent sprinkler",
        "nr",
        0.40,
        "sprinkler_fitter",
        "company standard",
        "PS-2026",
    ),
    (
        "sprinkler",
        "",
        "",
        "Sprinkler, any other type",
        "nr",
        0.50,
        "sprinkler_fitter",
        "estimator judgement",
        "Sam Senior",
    ),
    (
        "gate_valve",
        150,
        "",
        "Gate valve assembly DN150",
        "nr",
        3.50,
        "pipefitter",
        "company standard",
        "PS-2026",
    ),
    ("fitting", "", "", "Fitting, any", "nr", 0.25, "pipefitter", "company standard", "PS-2026"),
)


def productivity_list(
    entries: tuple[tuple[Any, ...], ...] = ENTRIES, extra: list[tuple[Any, ...]] | None = None
) -> bytes:
    """The list as a workbook. `extra` adds rows, for a bad one."""
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    assert sheet is not None  # noqa: S101 - a new workbook has one
    sheet.title = "Productivity"
    sheet.append(("Company productivity standards",))
    sheet.append(HEADER)
    for row in (*entries, *(extra or [])):
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
