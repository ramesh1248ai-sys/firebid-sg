"""The per-sheet budget that keeps NFR-01 achievable (P1-03).

NFR-01: 300 sheets through ingestion and classification within an hour on 4 workers, so each
sheet has at most 3,600 x 4 / 300 = 48 worker-seconds for everything. Geometry and views are
one part of that. This checks one dense sheet through both against a tenth of the budget,
which leaves the rest for rendering, title blocks and classification. The full 300-sheet run
is `pdf_benchmark throughput`, recorded in the build log.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from firebid.evals import pdf_benchmark

pytestmark = pytest.mark.req("NFR-01")

PER_SHEET_SECONDS = (
    pdf_benchmark.NFR01_SECONDS * pdf_benchmark.NFR01_WORKERS / pdf_benchmark.NFR01_SHEETS
)


def test_the_budget_is_what_nfr_01_leaves_each_sheet() -> None:
    assert PER_SHEET_SECONDS == 48.0


def test_a_dense_sheet_gets_geometry_and_views_well_within_its_budget(tmp_path: Path) -> None:
    sheet = tmp_path / "dense-1-A1.pdf"
    pdf_benchmark.dense_sheet(sheet, seed=1, paper="A1")

    seconds = pdf_benchmark._one_sheet(str(sheet))

    assert seconds < PER_SHEET_SECONDS / 10


def test_throughput_reports_a_projection_against_the_hour(tmp_path: Path) -> None:
    sheet = tmp_path / "dense-1-A1.pdf"
    pdf_benchmark.dense_sheet(sheet, seed=1, paper="A1")

    report = pdf_benchmark.throughput([sheet], count=2, workers=1)

    assert report["sheets"] == 2
    assert report["within_budget"] is True
    assert report["projected_300_on_4_workers_seconds"] < pdf_benchmark.NFR01_SECONDS
