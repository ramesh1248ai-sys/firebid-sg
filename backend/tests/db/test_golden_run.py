# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""The platform against a golden reference package, through the database (FR-LRN-01).

Each synthetic tender goes through the pipeline as uploaded documents do, a person confirms
the legend, detection and takeoff run, and the bid's stage outputs are exported and set
against its package: stages 1 to 7, and for the basement car park stages 8 to 12 as well,
to an estimate under review.

What the platform differs on is listed here in full, case by case, so that a difference
appearing, or one going away, is a change someone looks at: the defects, the differences to
settle against a recorded ambiguity, and what the run has that the package does not.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from firebid.db.models.core import Bid, Organisation
from firebid.db.models.workflow import Approval
from firebid.evals import (
    export_run,
    golden,
    synthetic_labour,
    synthetic_qto,
    synthetic_rates,
    synthetic_systems,
)
from firebid.evals.synthetic_network import NETWORK
from firebid.evals.synthetic_spec import CAR_PARK_NOTES, specification_docx, with_risks
from firebid.ingest.scanning import AlwaysCleanScanner
from firebid.services import boq, costing, labour, pricing, qto, risk, specs
from firebid.services import spec_analysis as analysis
from firebid.services.classification import classify_in_sandbox
from firebid.services.detection import detect_bid
from firebid.services.ingestion import Ingestor
from firebid.storage.object_store import MemoryObjectStore
from tests.db.test_boq import built, client_workbook, verify_takeoff
from tests.db.test_detection_pipeline import confirm_legend
from tests.db.test_qto import tender  # noqa: F401
from tests.db.test_risk import Team
from tests.db.test_symbol_mapping import from_consultant, no_tiles, read, store  # noqa: F401

pytestmark = [pytest.mark.usefixtures("no_tiles"), pytest.mark.req("FR-LRN-01")]

PACKAGES = Path(__file__).resolve().parents[3] / "eval" / "golden" / "synthetic"

# Each is the platform's to fix, the package's to correct, or the test's own, and is in the
# build log.
KNOWN_DEFECTS: dict[str, set[tuple[str, str, str]]] = {
    "TC-SYN-001": set(),
    "TC-SYN-002": {
        # The test's own: the only rate for this tee names a brand the line does not, which
        # the platform leaves for the model to propose, and no model runs here.
        ("STG-010", "line / tee_100x50 / price_status", "CRITICAL"),
        # The platform builds the checklist for the hose reel and hydrant systems the
        # specification has sections for; the package, for the sprinkler system drawn.
        ("STG-012", "open / checklist_items_open", "HIGH"),
        # The platform flags the five lines the client's bill has no item for, with the two
        # client lines; the package counts the client's two.
        ("STG-012", "open / bill_variances_flagged", "HIGH"),
    },
    "TC-SYN-003": {
        # A level where the package gives none and has no ambiguity for it: for a person.
        # The platform puts the breeching inlet, which only the schematic draws, on the level
        # the schematic names nearest it (L01).
        ("STG-007", "breeching_inlet / no / level", "HIGH"),
        # The package has each fire pump's driver (electric, diesel), which the schedule
        # gives in the pump's description ("ELECTRIC FIRE PUMP") and in no column of its
        # own. The platform reads the columns, and so states no driver.
        ("STG-007", "fire_pump / no / driver", "HIGH"),
    },
}
# B1: does every head take a drop, or the pendents only? The package records what the other
# reading gives at each stage, and the platform gives exactly that, to the cent.
THE_OTHER_READING_OF_B1 = {
    ("STG-009", "line / pipe_25_drop / quantity"),
    ("STG-009", "client / B4 / measured_quantity"),
    ("STG-009", "client / B4 / variance_percent"),
    ("STG-010", "line / pipe_25_drop / amount"),
    ("STG-010", "line / pipe_25_drop / labour hours"),
    ("STG-010", "line / pipe_25_drop / labour cost"),
    ("STG-010", "labour / hours"),
    ("STG-010", "labour / cost"),
    *{
        ("STG-010", f"build-up / {name}")
        for name in (
            "materials",
            "wastage",
            "labour",
            "direct",
            "cost",
            "margin",
            "total_excluding_gst",
            "gst",
            "total_including_gst",
        )
    },
    *{
        ("STG-011", f"risk / {kind} / impact {of}")
        for kind in ("night_work", "occupied_building", "basement")
        for of in ("hours", "cost")
    },
    *{
        ("STG-012", f"figure / {name}")
        for name in ("priced_bill", "direct", "total_excluding_gst", "gst", "total_including_gst")
    },
}
# Differences on what a package records as ambiguous: for a person to settle.
TO_SETTLE: dict[str, set[tuple[str, str, str]]] = {
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
        *{(stage, what, "B1") for stage, what in THE_OTHER_READING_OF_B1},
    },
    # C2 and C3 are recorded as open, and the platform gives what the package gives.
    "TC-SYN-003": {
        # C8: the platform puts the site plan, and what is taken off from it, on a level it
        # reads from the drawing number (SITE); the package gives them none. The register
        # and the takeoff agree with each other.
        ("STG-002", "fp-site-001 / level", "C8"),
        ("STG-007", "hydrant / no / level", "C8"),
        ("STG-007", "pipe / DN150 / m / level", "C8"),
        ("STG-007", "fitting / tee / DN150x150 / no / level", "C8"),
        # And so none of the main or its tees is on no level, where the package has the
        # site's share there. The share on B1 agrees.
        ("STG-007", "pipe / DN150 / m / on no level", "C8"),
        ("STG-007", "fitting / tee / DN150x150 / no / on no level", "C8"),
        # C6: the platform puts the pump room's pipe with the rising main (wet riser); the
        # package names it for the pumps. The floor's and the site's pipe agree.
        ("STG-007", "pipe / DN200 / m / system", "C6"),
        ("STG-007", "pipe / DN150 / m / system", "C6"),
        ("STG-007", "pipe / DN50 / m / system", "C6"),
    },
}
OTHER_SYSTEMS = ("hose_reel", "hydrant")
# In the run and not in the package: each is for a person to say whether the package left it
# out or the platform made it up.
FOR_REVIEW: dict[str, set[tuple[str, str]]] = {
    "TC-SYN-001": set(),
    "TC-SYN-002": {
        # Section 8 repeats two obligations of section 6.
        ("STG-008", "obligation / submittals / clause 8.2"),
        ("STG-008", "obligation / authority / clause 8.3"),
        # Allowed by the package: a further row for the hydrant system only.
        ("STG-008", "scope / hydrant / excavation"),
        # A clarification candidate for each line the client's bill has no item for, and for
        # each scope row that is unclear. The package lists the issues and the two variances.
        *{
            ("STG-011", f"candidate / bill variance / {line}")
            for line in ("tee_100x50", "tee_150x50", "hanger_50", "hanger_100", "hanger_150")
        },
        *{
            ("STG-011", f"candidate / scope / {system}:interface:{row}")
            for system in ("sprinkler", *OTHER_SYSTEMS)
            for row in ("ceiling_access", "drainage")
        },
        ("STG-011", "candidate / scope / hydrant:interface:excavation"),
        # The checklist for the two systems that are specified and not drawn.
        *{
            ("STG-011", f"checklist / {system} / {item}")
            for system, own in zip(OTHER_SYSTEMS, ("hose_reels", "hydrants"), strict=True)
            for item in (
                "pumps",
                "tanks",
                own,
                "hydraulic_calculations",
                "shop_drawings",
                "testing_commissioning",
                "authority_fsc",
                "builders_works",
                "power_supply",
            )
        },
    },
    "TC-SYN-003": set(),
}


@pytest.fixture
def car_park(session: Session, bid: Bid, store: MemoryObjectStore) -> Bid:
    """TC-SYN-002: the specification, the basement car park plan and the client's bill,
    read and taken off."""
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
    boq.read_client_boq(session, store, workbook)
    session.execute(text("DELETE FROM procrastinate_jobs"))
    session.commit()
    return bid


@pytest.fixture
def under_review(session: Session, car_park: Bid, organisation: Organisation) -> Bid:
    """The scenario TC-SYN-002 fixes, with what its people have done and no more: the takeoff
    verified and G1 on record, the company bill built and the client's set against it, the
    rate and productivity lists imported, the bill priced by rule on 2026-10-04 for a tender
    due 2026-10-15 and valid 90 days, a margin of 8% on cost, and the checklist and the
    risks found. No specification value, rate proposal, labour multiplier, checklist item or
    risk is decided, and no model is asked anything."""
    bid, team = car_park, Team(session, organisation, car_park)
    analysis.analyse(session, bid.id)
    bid.submission_deadline = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    bid.tender_validity_days = 90
    verify_takeoff(session, bid)
    assert team.senior.id is not None
    session.add(
        Approval(
            bid_id=bid.id,
            gate="G1",
            decision="approved",
            approver_id=team.senior.id,
            approver_role="senior_estimator",
            decided_at=datetime.now(UTC),
        )
    )
    built(session, bid, team.estimator)
    boq.propose_mappings(session, bid.id)
    rates = pricing.import_rates(
        session, bid.organisation_id, synthetic_rates.rate_list(), team.senior, "rates.xlsx"
    )
    assert rates.imported, rates.problems
    labour.import_productivity(
        session,
        bid.organisation_id,
        synthetic_labour.productivity_list(),
        team.senior,
        "productivity.xlsx",
    )
    costing.set_priced_on(session, bid, date(2026, 10, 4), team.estimator)
    pricing.price_boq(session, bid, team.estimator)
    costing.enter(
        session,
        bid,
        team.senior,
        component="margin",
        basis="percentage",
        percent=Decimal(8),
        base="cost",
    )
    risk.build_checklist(session, bid, team.estimator)
    risk.find(session, bid, team.estimator)
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
    # Every stage the package has is compared: none is left out of the run.
    assert {s.status for s in result.stages} == {"compared"}, told
    assert {
        (d.stage_id, d.what, d.classification) for d in result.differences if d.is_defect
    } == KNOWN_DEFECTS[case], told
    assert {
        (d.stage_id, d.what, d.ambiguity)
        for d in result.differences
        if d.classification == "TO SETTLE"
    } == TO_SETTLE[case], told
    assert {
        (d.stage_id, d.what) for d in result.differences if d.classification == "FOR REVIEW"
    } == FOR_REVIEW[case], told
    return result, told


def checks(result: golden.Result) -> dict[str, tuple[int, int]]:
    return {s.stage_id: (s.checks, s.passed) for s in result.stages if s.status == "compared"}


def test_the_platform_s_stages_1_to_7_against_tc_syn_001(session: Session, tender: Bid) -> None:
    result, told = against(session, tender, "TC-SYN-001")

    assert len(result.stages) == 7
    # Every count and every drawn length of the installation is the reference's, and so is
    # where each object is: 11 counts, 19 counts by grid bay, and the place of each of the
    # 33 instances on the plan and the enlarged plan (the schematic has no grid).
    assert checks(result)["STG-005"] == (63, 63), told
    # Eight legend rows, and the sheet each is on.
    assert checks(result)["STG-004"] == (16, 16), told


def test_the_platform_s_twelve_stages_against_tc_syn_002(
    session: Session, under_review: Bid
) -> None:
    result, told = against(session, under_review, "TC-SYN-002")

    assert len(result.stages) == 12
    # Nothing differs from intake to the bill but the drop (B1): the documents, the sheet,
    # its legend, counts and lengths, the specification as read, and both bills.
    passed = checks(result)
    assert all(passed[f"STG-0{n:02d}"][0] == passed[f"STG-0{n:02d}"][1] for n in range(1, 10))
    assert passed["STG-008"][0] >= 70 and passed["STG-009"][0] >= 110, told
    # Where B1 changes a figure, the platform gives what the package worked out by hand for
    # the other reading: SGD 6,454.39 before GST and 7,035.29 with it.
    other = {(d.stage_id, d.what) for d in result.differences if d.other_reading}
    assert other == THE_OTHER_READING_OF_B1, told


def test_the_platform_s_stages_1_to_7_against_tc_syn_003(session: Session, pump_room: Bid) -> None:
    result, told = against(session, pump_room, "TC-SYN-003")

    assert len(result.stages) == 7
    # No count, length or derived quantity differs: 17 pieces of equipment once each, and
    # the pipe of three plans. What differs is the level of four of the 28 kinds of item,
    # the system the pump room's pipe is named for, and the fire pumps' driver.
    passed = checks(result)
    assert all(passed[f"STG-00{n}"][0] == passed[f"STG-00{n}"][1] for n in range(1, 7)), told
    assert passed["STG-007"][0] - passed["STG-007"][1] == 2, told
    # 16 counts, 16 counts by grid bay, and the place and tag of each of the 16 instances on
    # the three plans.
    assert passed["STG-005"] == (48, 48), told
    assert all(
        d.what.endswith(("/ level", "/ on no level", "/ system", "/ driver"))
        for d in result.differences
    ), told
    # The six with C8 are its other reading: on the sheet, on its items, and in how much of
    # an item that is on two levels is on each. The three with C6 are its other reading.
    assert sum(d.other_reading for d in result.differences) == 9, told


def test_a_bid_with_nothing_read_exports_nothing(session: Session, bid: Bid) -> None:
    package = golden.load(PACKAGES / "TC-SYN-001")

    run = export_run.export(session, bid.id)

    assert run == {}
    result = golden.compare(package, run)
    assert {s.status for s in result.stages} == {"not exported"}
    assert golden.score(result).overall is None
