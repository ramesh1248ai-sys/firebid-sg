"""Whose document is it? Proposed from a file's path (FR-DOC-10).

The paths are the shapes found in a real tender folder: the client's drawing set, the
company's marked-up copies carrying the same drawing numbers, and its earlier responses.
"""

from __future__ import annotations

import pytest

from firebid.ingest.origin import Origin, clean_path, propose

pytestmark = pytest.mark.req("FR-DOC-10")


@pytest.mark.parametrize(
    "path",
    [
        "MOH/SPK  Drawing/L10/60743399_ACM_TN_TO_D_MFP_A03-10-01.pdf",
        "MOH/Specification/Section 21 Fire Protection.docx",
        "MOH/Combined schematics.pdf",
        "tender/Addendum 2/BOQ.xlsx",
        "FP-L05-201.dxf",
    ],
)
def test_a_client_issued_file_is_a_tender_document(path: str) -> None:
    assert propose(path).origin is Origin.TENDER


@pytest.mark.parametrize(
    "path",
    [
        "MOH/Rev B/L10/60743399_ACM_TN_TO_D_MFP_A03-10-01_SJME-B.pdf",
        "MOH/Upd/B1/60743399_ACM_TN_BM_D_MFP_A03-B1-02_SJME-A.pdf",
        "MOH/SJME_TTSH_layout.dxf",
        "MOH/Mark-ups/L3.pdf",
    ],
)
def test_a_file_with_the_company_s_mark_is_our_working_document(path: str) -> None:
    proposal = propose(path)

    assert proposal.origin is Origin.WORKING
    assert "our working document" in proposal.reason


@pytest.mark.parametrize(
    "path",
    [
        "MOH/SJME_TTSH_FP_RFP_Response_v7.xlsx",
        "MOH/Tender Review.docx",
        "MOH/RFI register.xlsx",
        "MOH/Submission/cover letter.docx",
    ],
)
def test_a_response_or_review_is_reference(path: str) -> None:
    # A response workbook also carries the company's mark: being a response decides first
    # only where the rules say so, and either way it is not read as tender input.
    assert propose(path).origin in (Origin.REFERENCE, Origin.WORKING)
    assert propose(path).origin is not Origin.TENDER


@pytest.mark.parametrize(
    "path", ["MOH/~$FP RFP Response.xlsx", "MOH/L10/Thumbs.db", "MOH/.DS_Store", "MOH/old.bak"]
)
def test_a_lock_or_system_file_is_ignored(path: str) -> None:
    assert propose(path).origin is Origin.IGNORED


def test_a_name_that_only_contains_the_letters_is_not_the_company_s_mark() -> None:
    assert propose("tender/MISJMEX-plan.pdf").origin is Origin.TENDER
    assert propose("tender/RESPONSE TIME CALCULATION.pdf").origin is Origin.TENDER


def test_a_path_is_a_label_never_a_place() -> None:
    assert clean_path("..\\..\\etc\\passwd") == "etc/passwd"
    assert clean_path("/MOH/./L10//A.pdf") == "MOH/L10/A.pdf"
    assert propose("../Rev B/A03-10-01_SJME-B.pdf").origin is Origin.WORKING
