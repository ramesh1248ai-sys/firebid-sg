# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""The platform against a golden reference package, through the database (FR-LRN-01).

TC-SYN-001's three sheets go through the pipeline as uploaded drawings do, a person
confirms the legend, detection and takeoff run, and the bid's stage outputs are exported
and set against the package.

The test holds the platform to having no critical and no high defect. What it differs on
below that is listed here in full, so that a difference appearing, or one going away, is a
change someone looks at.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from firebid.db.models.core import Bid
from firebid.evals import export_run, golden
from tests.db.test_qto import tender  # noqa: F401
from tests.db.test_symbol_mapping import no_tiles, store  # noqa: F401

pytestmark = [pytest.mark.usefixtures("no_tiles"), pytest.mark.req("FR-LRN-01")]

PACKAGES = Path(__file__).resolve().parents[3] / "eval" / "golden" / "synthetic"

# Open on 2026-10-08. Each is the platform's to fix or the package's to correct against the
# drawing, and is in the build log.
KNOWN_DEFECTS = {
    # The drawn reducer is held at the size of the pipe it sits on; its outlet size (100)
    # is not held.
    ("STG-007", "fitting / reducer / no / size", "MEDIUM"),
}
# Differences on what the package records as ambiguous: for a person to settle.
TO_SETTLE = {
    # A1: the platform gives every head a drop (12.0 m); the package, the pendents (8.0 m).
    ("STG-007", "pipe / DN25 / drop / m", "A1"),
    # A2: the platform leaves the enlarged plan's scale unverified, so does not measure it.
    ("STG-003", "fp-l05-301 / view 1 / measurable", "A2"),
    ("STG-003", "fp-l05-301 / view 1 / scale_verdict", "A2"),
    ("STG-006", "fp-l05-301 / measured", "A2"),
    ("STG-006", "fp-l05-301 / DN150 / length mm", "A2"),
    ("STG-006", "fp-l05-301 / DN50 / length mm", "A2"),
}


def test_the_platform_s_stages_1_to_7_against_tc_syn_001(session: Session, tender: Bid) -> None:
    package = golden.load(PACKAGES / "TC-SYN-001")

    run = export_run.export(session, tender.id)
    result = golden.compare(package, run)

    told = golden.report(result, golden.score(result))
    compared = {s.stage_id for s in result.stages if s.status == "compared"}
    assert compared == {f"STG-00{n}" for n in range(1, 8)}, told
    assert (result.defects()["CRITICAL"], result.defects()["HIGH"]) == (0, 0), told
    assert {
        (d.stage_id, d.what, d.classification) for d in result.differences if d.is_defect
    } == KNOWN_DEFECTS, told
    assert {
        (d.stage_id, d.what, d.ambiguity)
        for d in result.differences
        if d.classification == "TO SETTLE"
    } == TO_SETTLE, told
    # Nothing in the run that the reference does not have.
    assert not [d for d in result.differences if d.classification == "FOR REVIEW"], told
    # Every count and every drawn length of the installation is the reference's.
    by_stage = {s.stage_id: s for s in result.stages}
    assert by_stage["STG-005"].checks == by_stage["STG-005"].passed == 11
    assert by_stage["STG-004"].checks == by_stage["STG-004"].passed == 8


def test_a_bid_with_nothing_read_exports_nothing(session: Session, bid: Bid) -> None:
    package = golden.load(PACKAGES / "TC-SYN-001")

    run = export_run.export(session, bid.id)

    assert run == {}
    result = golden.compare(package, run)
    assert {s.status for s in result.stages} == {"not exported"}
    assert golden.score(result).overall is None
