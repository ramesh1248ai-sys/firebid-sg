# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""The platform against a golden reference package, through the database (FR-LRN-01).

Each synthetic tender goes through the pipeline as uploaded documents do, a person confirms
the legend, detection and takeoff run, and the bid's stage outputs are exported and set
against its package: stages 1 to 7.

The tests hold the platform to having no critical and no high defect. What it differs on
below that is listed here in full, case by case, so that a difference appearing, or one
going away, is a change someone looks at.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from firebid.db.models.core import Bid
from firebid.evals import export_run, golden, synthetic_qto, synthetic_systems
from firebid.evals.synthetic_network import NETWORK
from firebid.evals.synthetic_spec import CAR_PARK_NOTES, specification_docx, with_risks
from firebid.ingest.scanning import AlwaysCleanScanner
from firebid.services import qto, specs
from firebid.services.classification import classify_in_sandbox
from firebid.services.detection import detect_bid
from firebid.services.ingestion import Ingestor
from firebid.storage.object_store import MemoryObjectStore
from tests.db.test_boq import client_workbook
from tests.db.test_detection_pipeline import confirm_legend
from tests.db.test_qto import tender  # noqa: F401
from tests.db.test_symbol_mapping import from_consultant, no_tiles, read, store  # noqa: F401

pytestmark = [pytest.mark.usefixtures("no_tiles"), pytest.mark.req("FR-LRN-01")]

PACKAGES = Path(__file__).resolve().parents[3] / "eval" / "golden" / "synthetic"

# Open on 2026-10-08. Each is the platform's to fix or the package's to correct against the
# drawing, and is in the build log.
REDUCER = ("STG-007", "fitting / reducer / no / size", "MEDIUM")
KNOWN_DEFECTS = {
    # The drawn reducer is held at the size of the pipe it sits on; its outlet size (100)
    # is not held. The basement car park draws the same reducer.
    "TC-SYN-001": {REDUCER},
    "TC-SYN-002": {REDUCER},
    "TC-SYN-003": set(),
}
# Differences on what the package records as ambiguous: for a person to settle.
TO_SETTLE = {
    "TC-SYN-001": {
        # A1: the platform gives every head a drop (12.0 m); the package, the pendents (8.0 m).
        ("STG-007", "pipe / DN25 / drop / m", "A1"),
        # A2: the platform leaves the enlarged plan's scale unverified, so does not measure it.
        ("STG-003", "fp-l05-301 / view 1 / measurable", "A2"),
        ("STG-003", "fp-l05-301 / view 1 / scale_verdict", "A2"),
        ("STG-006", "fp-l05-301 / measured", "A2"),
        ("STG-006", "fp-l05-301 / DN150 / length mm", "A2"),
        ("STG-006", "fp-l05-301 / DN50 / length mm", "A2"),
    },
    "TC-SYN-002": {
        # B1, which is A1 again: 24 heads x 450 mm (10.8 m) against 16 pendents (7.2 m).
        ("STG-007", "pipe / DN25 / drop / m", "B1"),
    },
    # C2 and C3 are recorded as open, and the platform gives what the package gives.
    "TC-SYN-003": set(),
}


@pytest.fixture
def car_park(session: Session, bid: Bid, store: MemoryObjectStore) -> Bid:
    """TC-SYN-002: the specification, the basement car park plan and the client's bill."""
    payload = specification_docx(clauses=with_risks())
    specification = (
        Ingestor(session, store, AlwaysCleanScanner(), bid_id=bid.id)
        .ingest("Particular Specification Fire Protection.docx", payload)
        .stored[0]
    )
    classify_in_sandbox(session, specification, payload)
    revision = specs.read_specification(session, store, specification)
    assert revision is not None
    revision.state = "current"
    from_consultant(session, bid, NETWORK.name)
    plan, _ = synthetic_qto.car_park_plan("FP-B1-201", CAR_PARK_NOTES)
    read(session, bid, store, "FP-B1-201", plan)
    workbook = client_workbook(session, bid, store)
    # Read and classified: what the parse job marks as done (`jobs.tasks.parse_document`).
    specification.state = workbook.state = "done"
    confirm_legend(session, bid, store)
    detect_bid(session, store, bid.id)
    qto.recompute(session, bid.id)
    session.execute(text("DELETE FROM procrastinate_jobs"))
    session.commit()
    return bid


@pytest.fixture
def pump_room(session: Session, bid: Bid, store: MemoryObjectStore) -> Bid:
    """TC-SYN-003: the pump room, a typical floor, the site plan and the riser schematic."""
    from_consultant(session, bid, synthetic_systems.DELTA.name)
    for document, truth in synthetic_systems.tender():
        read(session, bid, store, truth.number, document)
    confirm_legend(session, bid, store, synthetic_systems.DESCRIBED)
    detect_bid(session, store, bid.id)
    qto.recompute(session, bid.id)
    session.execute(text("DELETE FROM procrastinate_jobs"))
    session.commit()
    return bid


def against(session: Session, bid: Bid, case: str) -> tuple[golden.Result, str]:
    """The bid set against the case's package, held to the differences listed above."""
    result = golden.compare(golden.load(PACKAGES / case), export_run.export(session, bid.id))
    told = golden.report(result, golden.score(result))
    compared = {s.stage_id for s in result.stages if s.status == "compared"}
    assert compared == {f"STG-00{n}" for n in range(1, 8)}, told
    assert (result.defects()["CRITICAL"], result.defects()["HIGH"]) == (0, 0), told
    assert {
        (d.stage_id, d.what, d.classification) for d in result.differences if d.is_defect
    } == KNOWN_DEFECTS[case], told
    assert {
        (d.stage_id, d.what, d.ambiguity)
        for d in result.differences
        if d.classification == "TO SETTLE"
    } == TO_SETTLE[case], told
    # Nothing in the run that the reference does not have.
    assert not [d for d in result.differences if d.classification == "FOR REVIEW"], told
    return result, told


def checks(result: golden.Result) -> dict[str, tuple[int, int]]:
    return {s.stage_id: (s.checks, s.passed) for s in result.stages if s.status == "compared"}


def test_the_platform_s_stages_1_to_7_against_tc_syn_001(session: Session, tender: Bid) -> None:
    result, told = against(session, tender, "TC-SYN-001")

    # Every count and every drawn length of the installation is the reference's.
    assert checks(result)["STG-005"] == (11, 11), told
    assert checks(result)["STG-004"] == (8, 8), told


def test_the_platform_s_stages_1_to_7_against_tc_syn_002(session: Session, car_park: Bid) -> None:
    result, told = against(session, car_park, "TC-SYN-002")

    # The three documents, the sheet, its view, its legend, its counts and its lengths.
    assert {k: v for k, v in checks(result).items() if k < "STG-007"} == {
        "STG-001": (7, 7),
        "STG-002": (7, 7),
        "STG-003": (5, 5),
        "STG-004": (8, 8),
        "STG-005": (6, 6),
        "STG-006": (4, 4),
    }, told
    # Stages 8 to 12 are in the package and are not compared: never passed by default.
    later = {s.stage_id: s.status for s in result.stages if s.stage_id > "STG-007"}
    assert later == {f"STG-0{n:02d}": "not compared" for n in range(8, 13)}


def test_the_platform_s_stages_1_to_7_against_tc_syn_003(session: Session, pump_room: Bid) -> None:
    result, told = against(session, pump_room, "TC-SYN-003")

    # Nothing differs: 17 pieces of equipment once each, and the pipe of three plans.
    assert all(done == passed for done, passed in checks(result).values()), told
    assert checks(result)["STG-005"] == (16, 16), told
    assert golden.score(result).overall == 1.0


def test_a_bid_with_nothing_read_exports_nothing(session: Session, bid: Bid) -> None:
    package = golden.load(PACKAGES / "TC-SYN-001")

    run = export_run.export(session, bid.id)

    assert run == {}
    result = golden.compare(package, run)
    assert {s.status for s in result.stages} == {"not exported"}
    assert golden.score(result).overall is None
