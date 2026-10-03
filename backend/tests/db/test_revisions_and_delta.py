# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""An addendum revises a drawing: what changed, and what a person verifies again (P2-02).

The synthetic general arrangement is taken off at R01, every item verified and G1 approved.
Addendum 1 then brings R02, with its seeded changes (`synthetic_revision`). The revision
comparison reports exactly those; the takeoff keeps every verification the addendum did not
touch; the delta report says what moved; G1 is reopened for the changed items only; and the
addendum's affected-items query reaches the takeoff and the bill.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from firebid.auth.provisioning import Principal
from firebid.db.models.audit import AuditEvent
from firebid.db.models.commercial import ClientBoq, ClientBoqLine, ClientBoqMapping
from firebid.db.models.core import Bid, Organisation
from firebid.db.models.documents import Addendum, Document, SheetRevision
from firebid.db.models.takeoff import QtoItem
from firebid.domain.state_machines import Role
from firebid.evals import synthetic
from firebid.evals import synthetic_revision as fixture
from firebid.evals.synthetic_network import NETWORK
from firebid.ingest.scanning import AlwaysCleanScanner
from firebid.services import addenda, bids, boq, delta, qto, review_actions, revision_compare
from firebid.services import symbols as symbol_service
from firebid.services.detection import detect_bid
from firebid.services.geometry import extract_all
from firebid.services.ingestion import Ingestor
from firebid.services.sheets import process_document
from firebid.services.title_blocks import read_title_blocks
from firebid.services.views import detect_all
from firebid.storage.object_store import MemoryObjectStore
from tests.db.test_detection_pipeline import confirm_legend
from tests.db.test_review import name_every_unlisted_symbol
from tests.db.test_sheet_views import SignIn, app, member, sign_in  # noqa: F401
from tests.db.test_symbol_mapping import from_consultant, no_tiles, store  # noqa: F401

PENDENT = "Sprinkler, pendent"
BRANCH_50 = "Pipe, DN50, branch"
BRANCH_65 = "Pipe, DN65, branch"
MAIN_150 = "Pipe, DN150, main"
VALVE = "Gate valve, DN150"


def read(
    session: Session,
    bid: Bid,
    store: MemoryObjectStore,
    document: Any,
    package_id: uuid.UUID | None = None,
) -> None:
    """A drawing through the parse pipeline, in an addendum's package when one is given."""
    stored = (
        Ingestor(session, store, AlwaysCleanScanner(), bid_id=bid.id, tender_package_id=package_id)
        .ingest(f"{fixture.NUMBER}-{uuid.uuid4().hex[:6]}.dxf", synthetic.dxf_bytes(document))
        .stored[0]
    )
    sheets = process_document(session, store, stored).sheets
    read_title_blocks(session, store, stored, sheets)
    extract_all(session, store, stored, sheets)
    detect_all(session, store, sheets)
    symbol_service.read_all(session, store, sheets)
    session.commit()


def settle(session: Session, bid: Bid, store: MemoryObjectStore) -> qto.Outcome:
    confirm_legend(session, bid, store)
    detect_bid(session, store, bid.id)
    outcome = qto.recompute(session, bid.id)
    session.execute(text("DELETE FROM procrastinate_jobs"))
    session.commit()
    return outcome


@pytest.fixture
def senior(session: Session, organisation: Organisation, bid: Bid) -> Principal:
    return member(session, organisation, bid, "sam", Role.SENIOR_ESTIMATOR)


@pytest.fixture
def approved(
    session: Session, bid: Bid, store: MemoryObjectStore, senior: Principal
) -> Iterator[Bid]:
    """R01 taken off, every item verified, the bill built and G1 approved."""
    from_consultant(session, bid, NETWORK.name)
    read(session, bid, store, fixture.sheet("R01"))
    settle(session, bid, store)
    actor = senior.actor()
    name_every_unlisted_symbol(session, bid, actor)
    review_actions.accept(session, bid.id, [i.id for i in qto.live_items(session, bid.id)], actor)
    boq.build_company_boq(session, bid.id, actor)
    qto.approve_g1(session, bid, actor, "senior_estimator")
    session.commit()
    yield bid


@pytest.fixture
def revised(
    session: Session, approved: Bid, store: MemoryObjectStore, senior: Principal
) -> tuple[Bid, Addendum, qto.Outcome]:
    """Addendum 1 brings R02 of the drawing."""
    addendum = addenda.register_addendum(
        session,
        approved.id,
        number="1",
        issued_on=date(2026, 7, 1),
        summary=None,
        actor=senior.actor(),
    )
    read(session, approved, store, fixture.sheet("R02", revised=True), addendum.tender_package_id)
    outcome = settle(session, approved, store)
    return approved, addendum, outcome


def items(session: Session, bid: Bid) -> dict[str, QtoItem]:
    return {item.description: item for item in qto.live_items(session, bid.id)}


def current_sheet(session: Session) -> SheetRevision:
    return session.execute(
        select(SheetRevision).where(SheetRevision.state == "current")
    ).scalar_one()


@pytest.mark.req("FR-DOC-08")
class TestRevisionComparison:
    def test_the_diff_reports_exactly_the_seeded_changes(
        self, session: Session, revised: tuple[Bid, Addendum, qto.Outcome]
    ) -> None:
        bid, _, _ = revised
        new = current_sheet(session)

        found = revision_compare.compare(session, bid.id, new.sheet_id)

        assert (found.old.revision, found.new.revision) == ("R01", "R02")
        assert sorted(
            (c.change, c.kind, c.object_type, str(c.before if c.change == "changed" else None))
            for c in found.diff.changes
        ) == sorted((s.change, s.kind, s.object_type, str(s.before)) for s in fixture.SEEDED)
        assert found.diff.alignment.method == "grid"

    def test_the_api_gives_the_change_list_located_on_the_new_sheet(
        self,
        session: Session,
        revised: tuple[Bid, Addendum, qto.Outcome],
        senior: Principal,
        sign_in: SignIn,
    ) -> None:
        bid, _, _ = revised
        new = current_sheet(session)
        client = sign_in(senior)

        listed = client.get(f"/bids/{bid.id}/sheets/{new.sheet_id}/revisions").json()
        body = client.get(f"/bids/{bid.id}/sheets/{new.sheet_id}/revision-diff").json()

        assert [(r["revision"], r["state"]) for r in listed] == [
            ("R02", "current"),
            ("R01", "superseded"),
        ]
        assert body["counts"]["added"] == 1 and body["counts"]["removed"] == 1
        assert body["counts"]["changed"] == 2
        width = float((new.reading and 420.0) or 420.0)
        assert all(0 <= change["x"] <= width for change in body["changes"])
        resized = next(c for c in body["changes"] if c["kind"] == "run")
        assert resized["before"] == {"dn": 50} and resized["after"] == {"dn": 65}
        assert len(resized["points"]) >= 2

    def test_a_first_issue_has_nothing_to_be_compared_with(
        self, session: Session, approved: Bid, senior: Principal, sign_in: SignIn
    ) -> None:
        only = current_sheet(session)

        response = sign_in(senior).get(f"/bids/{approved.id}/sheets/{only.sheet_id}/revision-diff")

        assert response.status_code == 404
        assert "superseded no earlier revision" in response.json()["detail"]


@pytest.mark.req("FR-QTO-12")
class TestDeltaTakeoff:
    def test_unchanged_verified_items_keep_their_verification(
        self, session: Session, revised: tuple[Bid, Addendum, qto.Outcome]
    ) -> None:
        bid, _, _ = revised
        found = items(session, bid)

        for untouched in (MAIN_150, "Pipe, DN100, main", VALVE, "Check valve, DN150"):
            assert found[untouched].state == "verified", untouched
            assert found[untouched].version == 1, untouched
        assert found["Tee, DN100xDN50 (rule-derived: not drawn)"].state == "verified"

    def test_changed_items_are_proposed_again_with_their_previous_value(
        self, session: Session, revised: tuple[Bid, Addendum, qto.Outcome]
    ) -> None:
        bid, _, _ = revised
        found = items(session, bid)

        # A head added, and an upright now a pendent: 16 becomes 18.
        pendent = found[PENDENT]
        assert (pendent.state, pendent.version) == ("proposed", 2)
        assert pendent.net_quantity == Decimal(18)
        previous = session.execute(
            select(QtoItem).where(QtoItem.id == pendent.supersedes_id)
        ).scalar_one()
        assert (previous.state, previous.net_quantity) == ("superseded", Decimal(16))
        assert previous.human_id == pendent.human_id
        assert found["Sprinkler, upright"].net_quantity == 3
        assert found["Sprinkler, sidewall"].net_quantity == 3
        # The second branch is DN65 now: 12 m less of DN50, and a new item.
        assert found[BRANCH_50].net_quantity == Decimal("60.000")
        assert (found[BRANCH_65].state, found[BRANCH_65].version) == ("proposed", 1)

    def test_only_the_new_revision_was_read_and_only_changed_items_were_remade(
        self, session: Session, revised: tuple[Bid, Addendum, qto.Outcome], store: MemoryObjectStore
    ) -> None:
        bid, _, outcome = revised

        assert outcome.unchanged >= 6 and outcome.created >= 5
        # Detecting the bid again finds every sheet as it was, and takeoff changes nothing.
        again_detected = detect_bid(session, store, bid.id)
        assert [sheet.unchanged for sheet in again_detected] == [True, True]
        again = qto.recompute(session, bid.id)
        assert (again.created, again.superseded) == (0, 0)

    def test_the_delta_report_matches_the_seeded_changes(
        self, session: Session, revised: tuple[Bid, Addendum, qto.Outcome]
    ) -> None:
        bid, _, _ = revised

        baseline, report = delta.report(session, bid.id)

        assert baseline.reason == "before Addendum 1"
        changes = {i.description: (i.change, i.before, i.after) for i in report.changed()}
        assert changes[PENDENT] == ("changed", Decimal(16), Decimal(18))
        assert changes["Sprinkler, upright"] == ("changed", Decimal(4), Decimal(3))
        assert changes["Sprinkler, sidewall"] == ("changed", Decimal(4), Decimal(3))
        assert changes[BRANCH_50] == ("changed", Decimal("72.000"), Decimal("60.000"))
        assert changes[BRANCH_65] == ("added", None, Decimal("12.000"))
        assert MAIN_150 not in changes and VALVE not in changes
        counts = report.counts()
        assert counts["removed"] == 0 and counts["to_review"] == len(report.changed())
        assert counts["verification_kept"] == counts["unchanged"]

    def test_quantity_changes_are_reported_per_boq_line(
        self, session: Session, revised: tuple[Bid, Addendum, qto.Outcome]
    ) -> None:
        bid, _, _ = revised

        _, report = delta.report(session, bid.id)

        heads = next(line for line in report.lines if "Pendent sprinkler head" in line.line)
        assert (heads.before, heads.after, heads.difference) == (
            Decimal(16),
            Decimal(18),
            Decimal(2),
        )
        branch = next(line for line in report.lines if "50 mm pipe" in line.line)
        assert branch.difference == Decimal("-12.000")
        # An item the bill does not hold yet is listed under its own heading.
        assert any(line.line == "(not in the bill)" for line in report.lines)

    def test_g1_is_reopened_for_the_changed_items_only(
        self,
        session: Session,
        revised: tuple[Bid, Addendum, qto.Outcome],
        senior: Principal,
    ) -> None:
        bid, _, outcome = revised
        found = items(session, bid)

        status = delta.gate_status(session, bid.id)

        assert status["status"] == "reopened"
        assert found[PENDENT].human_id in status["reopened_items"]
        assert found[MAIN_150].human_id not in status["reopened_items"]
        assert set(outcome.reopened) == set(status["reopened_items"])
        assert sorted(status["to_verify"]) == sorted(
            i.human_id for i in found.values() if i.state == "proposed"
        )
        assert not boq.g2_blockers(session, bid.id).g1_approved
        [summary] = [s for s in bids.dashboard(session, senior) if s.bid.id == bid.id]
        assert "G1" not in summary.gates_passed
        reopened = session.execute(
            select(AuditEvent).where(AuditEvent.action == "gate G1: reopened")
        ).scalar_one()
        assert reopened.bid_id == bid.id

    def test_verifying_the_changed_items_is_all_g1_needs_again(
        self, session: Session, revised: tuple[Bid, Addendum, qto.Outcome], senior: Principal
    ) -> None:
        bid, _, _ = revised
        actor = senior.actor()
        with pytest.raises(qto.QtoError, match="% of items verified"):
            qto.approve_g1(session, bid, actor, "senior_estimator")
        waiting = [i.id for i in qto.live_items(session, bid.id) if i.state == "proposed"]

        review_actions.accept(session, bid.id, waiting, actor)
        qto.approve_g1(session, bid, actor, "senior_estimator")

        assert delta.gate_status(session, bid.id)["status"] == "approved"
        assert delta.snapshots(session, bid.id)[0].reason == "G1 approved"

    def test_the_api_serves_the_report_and_the_gate(
        self,
        session: Session,
        revised: tuple[Bid, Addendum, qto.Outcome],
        senior: Principal,
        sign_in: SignIn,
    ) -> None:
        bid, _, _ = revised
        client = sign_in(senior)

        baselines = client.get(f"/bids/{bid.id}/qto/baselines").json()
        body = client.get(f"/bids/{bid.id}/qto/delta").json()

        assert [b["reason"] for b in baselines] == ["before Addendum 1", "G1 approved"]
        assert body["gate"]["status"] == "reopened"
        pendent = next(i for i in body["items"] if i["description"] == PENDENT)
        assert (pendent["before"], pendent["after"], pendent["to_review"]) == (
            "16.000",
            "18.000",
            True,
        )

    def test_a_bid_with_no_baseline_says_when_one_is_taken(
        self, session: Session, bid: Bid, senior: Principal, sign_in: SignIn
    ) -> None:
        response = sign_in(senior).get(f"/bids/{bid.id}/qto/delta")

        assert response.status_code == 404 and "G1 is approved" in response.json()["detail"]


@pytest.mark.req("FR-DOC-05")
@pytest.mark.req("FR-QTO-12")
class TestWhatTheAddendumChanged:
    def test_the_affected_items_reach_the_takeoff_and_the_bill(
        self, session: Session, revised: tuple[Bid, Addendum, qto.Outcome]
    ) -> None:
        bid, addendum, _ = revised
        found = items(session, bid)

        affected = addenda.affected_items(session, addendum)

        by_kind: dict[str, list[addenda.AffectedItem]] = {}
        for item in affected:
            by_kind.setdefault(item.kind, []).append(item)
        assert [(s.reference, s.revision, s.replaces) for s in by_kind["sheet"]] == [
            (fixture.NUMBER, "R02", "R01")
        ]
        takeoff = {item.reference: item for item in by_kind["qto_item"]}
        pendent = takeoff[found[PENDENT].human_id]
        assert (pendent.state, pendent.replaces) == ("proposed", "16.000")
        assert pendent.detail["change"] == "changed" and pendent.id == found[PENDENT].id
        assert found[MAIN_150].human_id not in takeoff
        lines = {item.reference: item for item in by_kind["boq_line"]}
        assert any("Pendent sprinkler head" in reference for reference in lines)

    def test_a_flagged_client_line_resting_on_a_changed_item_is_a_candidate_again(
        self, session: Session, revised: tuple[Bid, Addendum, qto.Outcome]
    ) -> None:
        bid, addendum, _ = revised
        heads = next(
            line
            for line in boq.lines_of(session, boq.current_boq(session, bid.id))  # type: ignore[arg-type]
            if "Pendent sprinkler head" in line.description
        )
        document = (
            session.execute(select(Document).where(Document.bid_id == bid.id)).scalars().first()
        )
        assert document is not None
        client = ClientBoq(bid_id=bid.id, document_id=document.id, status="read")
        session.add(client)
        session.flush()
        line = ClientBoqLine(
            bid_id=bid.id,
            client_boq_id=client.id,
            row_index=7,
            item_no="B/3",
            description="Pendent sprinklers",
            unit="nr",
            quantity=Decimal(14),
        )
        session.add(line)
        session.flush()
        session.add(
            ClientBoqMapping(
                bid_id=bid.id,
                client_boq_line_id=line.id,
                boq_line_id=heads.id,
                measured_quantity=Decimal(16),
                variance_percent=14.3,
                flagged=True,
                state="confirmed",
            )
        )
        session.flush()

        affected = addenda.affected_items(session, addendum)

        [candidate] = [item for item in affected if item.kind == "clarification_candidate"]
        assert candidate.reference == "B/3 Pendent sprinklers"
        assert candidate.detail["client_quantity"] == "14"

    def test_an_addendum_before_any_takeoff_changes_no_takeoff(
        self, session: Session, bid: Bid, senior: Principal
    ) -> None:
        addendum = addenda.register_addendum(
            session, bid.id, number="1", issued_on=None, summary=None, actor=senior.actor()
        )

        assert delta.snapshots(session, bid.id) == []
        assert [i.kind for i in addenda.affected_items(session, addendum)] == []
