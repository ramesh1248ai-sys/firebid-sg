# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""Bid risk and qualifications through the database and the API (P2-07).

The specification with its design and site-conditions section is read, the basement car
park plan is taken off and billed, and the labour library is loaded, so the checklist
pre-fills from the scope matrix, the risks are found with their evidence, and an execution
risk's impact is worked out by the labour engine.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from firebid.auth.provisioning import Principal
from firebid.db.models.core import Bid, Organisation
from firebid.db.models.documents import DocumentRevision
from firebid.db.models.risk import Risk
from firebid.domain.actors import Actor
from firebid.domain.state_machines import BidState, Role, TransitionError
from firebid.evals import synthetic_labour, synthetic_qto
from firebid.evals.synthetic_network import NETWORK
from firebid.evals.synthetic_spec import (
    CAR_PARK_NOTES,
    EXPECTED_DESIGN_RISKS,
    EXPECTED_WORDING_RISKS,
    specification_docx,
    with_risks,
)
from firebid.ingest.scanning import AlwaysCleanScanner
from firebid.risk import rules
from firebid.services import boq, costing, labour, qto, specs
from firebid.services import clarifications as clarification_service
from firebid.services import risk as service
from firebid.services import spec_analysis as analysis
from firebid.services.classification import classify_in_sandbox
from firebid.services.detection import detect_bid
from firebid.services.ingestion import Ingestor
from firebid.services.transitions import apply_transition
from firebid.storage.object_store import MemoryObjectStore
from tests.db.test_boq import built, verify_takeoff
from tests.db.test_detection_pipeline import confirm_legend
from tests.db.test_sheet_views import SignIn, app, member, sign_in  # noqa: F401
from tests.db.test_spec_analysis import SHEET
from tests.db.test_symbol_mapping import from_consultant, no_tiles, read, store  # noqa: F401

pytestmark = pytest.mark.usefixtures("no_tiles")

TODAY = date(2026, 10, 4)


class Team:
    def __init__(self, session: Session, organisation: Organisation, bid: Bid) -> None:
        def one(name: str, role: Role) -> Principal:
            return member(session, organisation, bid, name, role)

        self.estimator_principal = one("edna", Role.ESTIMATOR)
        self.senior_principal = one("sam", Role.SENIOR_ESTIMATOR)
        self.manager_principal = one("bella", Role.BID_MANAGER)
        self.director_principal = one("carl", Role.COMMERCIAL_DIRECTOR)
        self.estimator: Actor = self.estimator_principal.actor()
        self.senior: Actor = self.senior_principal.actor()
        self.manager: Actor = self.manager_principal.actor()
        self.director: Actor = self.director_principal.actor()


@pytest.fixture
def risk_bid(
    session: Session, bid: Bid, organisation: Organisation, store: MemoryObjectStore
) -> tuple[Bid, Team]:
    """The specification with section 8 read and analysed, the basement plan taken off and
    billed, the productivity library loaded, and the bid priced on a known day."""
    payload = specification_docx(clauses=with_risks())
    document = (
        Ingestor(session, store, AlwaysCleanScanner(), bid_id=bid.id)
        .ingest("Particular Specification Fire Protection Rev C.docx", payload)
        .stored[0]
    )
    classify_in_sandbox(session, document, payload)
    revision = specs.read_specification(session, store, document)
    assert isinstance(revision, DocumentRevision)
    revision.state = "current"
    from_consultant(session, bid, NETWORK.name)
    plan, _ = synthetic_qto.car_park_plan(SHEET, CAR_PARK_NOTES)
    read(session, bid, store, SHEET, plan)
    confirm_legend(session, bid, store)
    detect_bid(session, store, bid.id)
    qto.recompute(session, bid.id)
    session.execute(text("DELETE FROM procrastinate_jobs"))
    analysis.analyse(session, bid.id)
    team = Team(session, organisation, bid)
    verify_takeoff(session, bid)
    built(session, bid, team.estimator)
    labour.import_productivity(
        session, organisation.id, synthetic_labour.productivity_list(), team.senior, "p.xlsx"
    )
    costing.set_priced_on(session, bid, TODAY, team.estimator)
    session.commit()
    return bid, team


def by_key(rows: list[Risk]) -> dict[str, Risk]:
    return {row.key: row for row in rows}


@pytest.mark.req("FR-RSK-01")
class TestChecklist:
    def test_the_checklist_pre_fills_from_the_scope_matrix_and_g3_waits_for_it(
        self, session: Session, risk_bid: tuple[Bid, Team]
    ) -> None:
        bid, team = risk_bid
        assert service.g3_readiness(session, bid.id).describe() == (
            "the scope-gap checklist is not built"
        )

        rows = service.build_checklist(session, bid, team.estimator)
        session.commit()

        found = {(row.system, row.item_key): row for row in rows}
        # From the scope matrix: clause 7.1 gives the power supply to others, 7.3 excludes
        # builder's works, 6.6 and 6.7 oblige the submittals and the authority support.
        assert found[("sprinkler", "power_supply")].status == "by_others"
        assert found[("sprinkler", "power_supply")].basis == "the scope matrix has it by others"
        assert found[("sprinkler", "power_supply")].evidence[0]["label"] == (
            "Specification clause 7.1"
        )
        assert found[("sprinkler", "builders_works")].status == "excluded"
        assert found[("sprinkler", "shop_drawings")].status == "included"
        assert found[("sprinkler", "hydraulic_calculations")].status == "included"
        assert found[("sprinkler", "authority_fsc")].status == "included"
        # Nothing in the specification or the takeoff settles the pumps or the tanks.
        assert found[("sprinkler", "pumps")].status == "open"
        assert found[("sprinkler", "tanks")].status == "open"
        assert all(row.decided_by is None for row in rows)

        readiness = service.g3_readiness(session, bid.id)
        waiting = {(c["system"], c["item"]) for c in readiness.open_checks}
        assert not readiness.ready and ("sprinkler", "pumps") in waiting
        team_bid_under_review(session, bid)
        with pytest.raises(TransitionError, match=r"not ready for G3: .* scope checklist item"):
            apply_transition(
                session, bid, target=BidState.APPROVED_FOR_SUBMISSION, actor=team.director
            )

        for row in rows:
            if row.status == "open":
                service.resolve_check(
                    session, bid, row, team.estimator, status="by_others", note="main contract"
                )
        session.commit()

        assert service.g3_readiness(session, bid.id).ready, "no risks found yet, nothing open"
        apply_transition(session, bid, target=BidState.APPROVED_FOR_SUBMISSION, actor=team.director)
        assert bid.state == "approved_for_submission"

    def test_a_person_s_resolution_survives_a_rebuild_and_a_change_needs_a_reason(
        self, session: Session, risk_bid: tuple[Bid, Team]
    ) -> None:
        bid, team = risk_bid
        rows = service.build_checklist(session, bid, team.estimator)
        pumps = next(r for r in rows if (r.system, r.item_key) == ("sprinkler", "pumps"))
        power = next(r for r in rows if (r.system, r.item_key) == ("sprinkler", "power_supply"))

        with pytest.raises(service.RiskError, match="included, excluded, by others or clarified"):
            service.resolve_check(session, bid, pumps, team.estimator, status="open", note="x")
        with pytest.raises(service.RiskError, match="say why"):
            service.resolve_check(session, bid, power, team.estimator, status="included", note="")
        service.resolve_check(
            session, bid, pumps, team.estimator, status="clarified", note="TC-004 asks the client"
        )
        service.build_checklist(session, bid, team.estimator)
        session.commit()

        assert (pumps.status, pumps.proposed_status) == ("clarified", "open")
        assert pumps.decided_by == team.estimator.label
        [event] = [
            e for e in service.history(session, "scope_check", pumps.id) if "resolved" in e.action
        ]
        assert event.before == {"status": "open"} and event.reason == "TC-004 asks the client"


def team_bid_under_review(session: Session, bid: Bid) -> None:
    """The bid as it stands when G3 is asked for (the earlier gates are tested elsewhere)."""
    bid.state = str(BidState.UNDER_REVIEW)
    session.flush()


@pytest.mark.req("FR-RSK-02")
class TestDesignResponsibility:
    def test_design_clauses_yield_cited_risks_with_a_proposed_treatment(
        self, session: Session, risk_bid: tuple[Bid, Team]
    ) -> None:
        bid, team = risk_bid

        found = by_key(service.find(session, bid, team.estimator))
        session.commit()

        design = {k: r for k, r in found.items() if r.category == "design_responsibility"}
        assert {
            row.kind: tuple(str(item["clause"]) for item in row.evidence) for row in design.values()
        } == dict(EXPECTED_DESIGN_RISKS)
        build = found["design:design_and_build"]
        assert build.evidence[0]["label"] == "Specification clause 8.1"
        assert "design and build basis" in build.evidence[0]["quote"]
        assert (build.proposed_treatment, build.treatment, build.status) == (
            "qualify",
            None,
            "open",
        )
        assert found["design:qp_engagement"].proposed_treatment == "price"
        # No engine prices design work: the impact says so and waits for the estimator.
        assert build.impact["method"] == "not_computed" and build.cost_allowance is None


@pytest.mark.req("FR-RSK-03")
class TestExecution:
    def test_seeded_conditions_are_flagged_with_evidence(
        self, session: Session, risk_bid: tuple[Bid, Team]
    ) -> None:
        bid, team = risk_bid
        level = next(item.level for item in qto.live_items(session, bid.id) if item.level)
        qto.set_parameter(
            session, bid.id, "ceiling_height_mm", Decimal(5200), team.estimator, level=level
        )
        qto.set_parameter(session, bid.id, "levels_served", Decimal(24), team.estimator)

        found = by_key(service.find(session, bid, team.estimator))
        session.commit()

        for kind, clause in EXPECTED_WORDING_RISKS:
            row = found[f"execution:{kind}"]
            assert [e["label"] for e in row.evidence] == [f"Specification clause {clause}"]
        assert (
            "at night between 2200 and 0600" in found["execution:night_work"].evidence[0]["quote"]
        )
        height = found[f"execution:work_at_height:{level}"]
        assert height.evidence[0]["label"] == f"Ceiling height, {level}"
        assert height.evidence[0]["quote"] == f"5200 mm (entered by {team.estimator.label})"
        assert found["execution:high_rise"].evidence[0]["label"] == "Levels served"
        # The plan is of basement 1: the level the drawings and the takeoff show.
        basement = found["execution:basement"]
        assert basement.detail["levels"] == ["B1"]
        assert basement.evidence == [
            {"kind": "level", "label": "Level B1 on the drawings", "quote": ""}
        ]
        assert all(row.evidence for row in found.values())

    @pytest.mark.req("FR-RSK-04")
    def test_a_level_s_risk_reaches_the_labour_on_that_level_in_lines_billed_for_the_building(
        self, session: Session, risk_bid: tuple[Bid, Team]
    ) -> None:
        bid, team = risk_bid
        found = by_key(service.find(session, bid, team.estimator))
        estimate = labour.estimate(session, bid, TODAY)
        worked = [line for line in estimate.lines if line.baseline_hours is not None]

        impact = found["execution:basement"].impact

        # Only the heads are billed by level; the pipe, fittings and valves are one line each
        # for the building. All of this bid is on B1, so the basement reaches all its labour.
        assert any(line.line.level is None for line in worked)
        assert f"over {len(worked)} bill line(s) on B1" in str(impact["basis"])
        assert Decimal(str(impact["hours"])) == (estimate.baseline_hours * Decimal("0.1")).quantize(
            Decimal("0.01")
        )

    def test_a_risk_no_longer_found_is_marked_and_kept(
        self, session: Session, risk_bid: tuple[Bid, Team]
    ) -> None:
        bid, team = risk_bid
        qto.set_parameter(session, bid.id, "levels_served", Decimal(24), team.estimator)
        service.find(session, bid, team.estimator)
        # A later request: parameters are ordered by when their transaction began, so the
        # second value must be set in one of its own to be the later of the two.
        session.commit()
        qto.set_parameter(session, bid.id, "levels_served", Decimal(6), team.estimator)

        live = by_key(service.find(session, bid, team.estimator))
        session.commit()

        assert "execution:high_rise" not in live
        kept = by_key(service.risks(session, bid.id, live=False))["execution:high_rise"]
        assert kept.status == "no_longer_found"


@pytest.mark.req("FR-RSK-04")
class TestImpact:
    def test_an_execution_risk_is_valued_by_the_labour_engine_and_adjusted_with_a_reason(
        self, session: Session, risk_bid: tuple[Bid, Team]
    ) -> None:
        bid, team = risk_bid
        night = by_key(service.find(session, bid, team.estimator))["execution:night_work"]
        estimate = labour.estimate(session, bid, TODAY)
        worked = [line for line in estimate.lines if line.baseline_hours is not None]
        hours = sum((line.baseline_hours or Decimal(0) for line in worked), Decimal(0))
        cost = sum(
            (
                (line.baseline_hours or Decimal(0)) * line.rate.hourly
                for line in worked
                if line.rate
            ),
            Decimal(0),
        )

        # Night work is x 1.20: a fifth more hours, at the trades' rates.
        assert night.impact["method"] == "labour_multiplier"
        assert Decimal(night.impact["factor"]) == Decimal("1.2") and hours > 0
        assert night.programme_hours == (hours * Decimal("0.20")).quantize(Decimal("0.01"))
        assert night.cost_allowance is not None
        assert night.cost_allowance.amount == (cost * Decimal("0.20")).quantize(Decimal("0.01"))
        assert night.impact_state == "computed"

        with pytest.raises(service.RiskError, match="say why"):
            service.decide_impact(session, bid, night, team.estimator, cost=Decimal(5000))
        service.decide_impact(
            session,
            bid,
            night,
            team.estimator,
            cost=Decimal(5000),
            hours=Decimal(60),
            reason="Only the podium is worked at night: about a third of the heads.",
        )
        session.commit()

        assert (night.impact_state, night.impact_by) == ("adjusted", team.estimator.label)
        assert night.cost_allowance.amount == Decimal("5000.00")
        [event] = [
            e
            for e in service.history(session, "risk", night.id)
            if e.action.startswith("risk: impact")
        ]
        assert event.reason == "Only the podium is worked at night: about a third of the heads."
        assert event.before is not None and event.before["state"] == "computed"
        # Finding the risks again does not undo what the estimator set.
        service.find(session, bid, team.estimator)
        assert night.cost_allowance.amount == Decimal("5000.00")

    def test_accepting_takes_the_computed_figure_and_the_estimate_then_carries_it(
        self, session: Session, risk_bid: tuple[Bid, Team]
    ) -> None:
        bid, team = risk_bid
        found = by_key(service.find(session, bid, team.estimator))
        night, build = found["execution:night_work"], found["design:design_and_build"]
        computed = night.cost_allowance

        service.decide_impact(session, bid, night, team.estimator, accept_computed=True)
        with pytest.raises(service.RiskError, match="nothing is computed for this risk"):
            service.decide_impact(session, bid, build, team.estimator, accept_computed=True)

        assert (night.impact_state, night.cost_allowance) == ("accepted", computed)
        # Once the multiplier is confirmed in the labour estimate, the risk adds nothing more.
        labour.decide(
            session,
            bid,
            team.estimator,
            key="night_work",
            level=None,
            state="confirmed",
            basis="specification clause 8.4",
        )
        again = service.computed_impact(session, bid, night, TODAY)
        assert (again.method, again.cost) == ("already_priced", Decimal("0.00"))

    def test_the_database_refuses_an_adjustment_without_a_reason(
        self, session: Session, risk_bid: tuple[Bid, Team]
    ) -> None:
        bid, team = risk_bid
        night = by_key(service.find(session, bid, team.estimator))["execution:night_work"]
        session.commit()

        with pytest.raises(IntegrityError, match="adjustment_reasoned"):
            session.execute(
                text(
                    "UPDATE risk SET impact_state = 'adjusted', impact_reason = ' ' WHERE id = :r"
                ),
                {"r": night.id},
            )
        session.rollback()


@pytest.mark.req("FR-RSK-05")
class TestQualifications:
    def test_every_entry_links_to_its_source_and_is_edited_with_a_history(
        self, session: Session, risk_bid: tuple[Bid, Team]
    ) -> None:
        bid, team = risk_bid
        service.build_checklist(session, bid, team.estimator)
        found = by_key(service.find(session, bid, team.estimator))
        build, night = found["design:design_and_build"], found["execution:night_work"]
        service.treat(session, bid, build, team.manager, treatment="qualify")
        service.treat(session, bid, night, team.manager, treatment="price")
        service.decide_impact(session, bid, night, team.estimator, accept_computed=True)
        boq.set_conventions(session, bid.id, {}, team.estimator)
        conflict = next(
            (c.kind, c.ref)
            for c in clarification_service.candidates(session, bid)
            if c.topic == "pipe_material"
        )
        asked = clarification_service.draft(session, bid, [conflict], team.estimator)
        clarification_service.prepare_submission(session, bid, team.manager)

        made = service.propose_qualifications(session, bid, team.manager)
        session.commit()
        entries = service.qualifications(session, bid.id)

        assert made and len(entries) == len(made) + 1
        by_source = {(e.source_kind, e.source_ref): e for e in entries}
        # From a risk to qualify, a risk priced, the checklist, the conventions, a clarification.
        qualified = by_source[("risk", str(build.id))]
        assert qualified.kind == "qualification"
        assert "Our offer makes no allowance for it (Specification clause 8.1)" in qualified.text
        assert by_source[("risk", str(night.id))].kind == "assumption"
        exclusions = [e for e in entries if e.kind == "exclusion"]
        assert {e.source_kind for e in exclusions} <= {"scope_check", "scope_row"}
        assert any("Builder's works (sprinkler) is excluded" in e.text for e in exclusions)
        assert any("is by others and is not included" in e.text for e in exclusions)
        conventions = [e for e in entries if e.source_kind == "measurement_convention"]
        assert conventions and all(e.kind == "assumption" for e in conventions)
        assert by_source[("clarification", str(asked.id))].clarification_id == asked.id
        # Every entry names a source the bid really has.
        for entry in entries:
            assert entry.source_kind in service.SOURCE_KINDS and entry.source_label
            assert service.source_label(session, bid, entry.source_kind, entry.source_ref)
        # Proposing again adds nothing, and leaves a person's wording alone.
        service.edit_qualification(
            session,
            bid,
            qualified,
            team.manager,
            text="Design responsibility is excluded; we install to the tender design.",
            kind="exclusion",
            note="as agreed with the director",
        )
        assert service.propose_qualifications(session, bid, team.manager) == []
        session.commit()

        assert qualified.text.startswith("Design responsibility is excluded")
        [edit] = service.history(session, "qualification", qualified.id)
        assert (
            edit.action == "qualification: edited" and edit.reason == "as agreed with the director"
        )
        assert edit.before is not None and edit.before["kind"] == "qualification"
        assert edit.after is not None and edit.after["kind"] == "exclusion"

    def test_a_person_s_own_entry_must_name_a_source_of_the_bid(
        self, session: Session, risk_bid: tuple[Bid, Team]
    ) -> None:
        bid, team = risk_bid
        shutdown = by_key(service.find(session, bid, team.estimator))["execution:shutdown"]

        with pytest.raises(service.RiskError, match="an entry links to its source"):
            service.add_qualification(
                session,
                bid,
                team.manager,
                kind="deviation",
                text="We deviate.",
                source_kind="risk",
                source_ref="not-a-risk",
            )
        row = service.add_qualification(
            session,
            bid,
            team.manager,
            kind="deviation",
            text="Shutdowns are limited to two, of four hours each.",
            source_kind="risk",
            source_ref=str(shutdown.id),
        )
        session.commit()

        assert (row.kind, row.source_label) == ("deviation", shutdown.title)
        with pytest.raises(IntegrityError, match="source_named"):
            session.execute(
                text("UPDATE qualification SET source_ref = '' WHERE id = :q"), {"q": row.id}
            )
        session.rollback()


@pytest.mark.req("FR-RSK-06")
class TestRegister:
    def test_the_register_tracks_owner_treatment_and_status_with_a_history(
        self, session: Session, risk_bid: tuple[Bid, Team]
    ) -> None:
        bid, team = risk_bid
        service.build_checklist(session, bid, team.estimator)
        for row in service.checks(session, bid.id):
            if row.status == "open":
                service.resolve_check(
                    session, bid, row, team.estimator, status="by_others", note="n"
                )
        found = service.find(session, bid, team.estimator)
        session.commit()
        build = by_key(found)["design:design_and_build"]

        readiness = service.g3_readiness(session, bid.id)
        assert len(readiness.untreated_risks) == len(found)
        assert readiness.describe() == f"{len(found)} risk(s) with no treatment"
        with pytest.raises(service.RiskError, match="price, qualify, clarify or accept"):
            service.treat(session, bid, build, team.manager, treatment="ignore")
        with pytest.raises(service.RiskError, match="give the risk a treatment"):
            service.treat(session, bid, build, team.manager, status="closed")

        service.treat(
            session,
            bid,
            build,
            team.manager,
            treatment="qualify",
            owner="Carl",
            owner_id=team.director.id,
            note="exclude design liability",
        )
        service.treat(session, bid, build, team.director, status="closed")
        for risk in found:
            if risk.treatment is None:
                service.treat(session, bid, risk, team.manager, treatment=risk.proposed_treatment)
        session.commit()

        assert (build.treatment, build.owner, build.status) == ("qualify", "Carl", "closed")
        events = service.history(session, "risk", build.id)
        assert [(e.actor_label, e.after) for e in events] == [
            (team.manager.label, {"treatment": "qualify", "owner": "Carl", "status": "treated"}),
            (team.director.label, {"treatment": "qualify", "owner": "Carl", "status": "closed"}),
        ]
        assert events[0].before == {"treatment": None, "owner": None, "status": "open"}
        assert events[0].reason == "exclude design liability"
        assert service.g3_readiness(session, bid.id).ready
        kinds = {row.kind for row in found}
        assert {k for k, _ in EXPECTED_DESIGN_RISKS} <= kinds
        assert all(row.proposed_treatment in rules.TREATMENTS for row in found)


class TestApi:
    @pytest.mark.req("FR-RSK-01")
    @pytest.mark.req("FR-RSK-06")
    def test_the_risk_page_goes_from_nothing_to_ready_for_g3(
        self, session: Session, risk_bid: tuple[Bid, Team], sign_in: SignIn
    ) -> None:
        bid, team = risk_bid
        base = f"/bids/{bid.id}/risk"
        client = sign_in(team.manager_principal)

        empty = client.get(base).json()
        built_page = client.post(f"{base}/checklist/build").json()
        found = client.post(f"{base}/find").json()
        waiting = client.get(f"{base}/g3").json()
        for check in found["checklist"]:
            if check["status"] == "open":
                client.put(
                    f"{base}/checklist/{check['id']}", json={"status": "clarified", "note": "TC"}
                )
        unreasoned = client.put(
            f"{base}/checklist/{found['checklist'][0]['id']}", json={"status": "open"}
        )
        for item in found["risks"]:
            client.put(
                f"{base}/risks/{item['id']}",
                json={"treatment": item["proposed_treatment"], "owner": "Bella"},
            )
        night = next(r for r in found["risks"] if r["key"] == "execution:night_work")
        adjusted = client.put(
            f"{base}/risks/{night['id']}/impact",
            json={"cost": "5000", "hours": "60", "reason": "podium only"},
        )
        no_reason = client.put(f"{base}/risks/{night['id']}/impact", json={"cost": "1"})
        proposed = client.post(f"{base}/qualifications/propose").json()
        history = client.get(f"{base}/risks/{night['id']}/history").json()
        ready = client.get(f"{base}/g3").json()

        assert empty["g3"]["summary"] == "the scope-gap checklist is not built"
        assert empty["statuses"] == ["included", "excluded", "by_others", "clarified"]
        assert {c["item_key"] for c in built_page["checklist"]} >= {"pumps", "power_supply"}
        assert waiting["ready"] is False and waiting["open_checks"] and waiting["untreated_risks"]
        assert unreasoned.status_code == 422
        assert adjusted.status_code == 200, adjusted.text
        saved = next(r for r in adjusted.json()["risks"] if r["id"] == night["id"])
        assert (saved["impact_state"], saved["cost_allowance"]) == ("adjusted", "5000.00")
        assert saved["impact"]["method"] == "labour_multiplier"
        assert no_reason.status_code == 422 and "say why" in no_reason.json()["detail"]
        assert proposed["qualifications"]
        assert all(q["source_kind"] and q["source_label"] for q in proposed["qualifications"])
        assert [h["action"] for h in history] == ["risk: register updated", "risk: impact adjusted"]
        assert ready == {
            "ready": True,
            "checklist_built": True,
            "open_checks": [],
            "untreated_risks": [],
            "summary": "",
        }
