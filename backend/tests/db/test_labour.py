# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""Labour estimation through the database and the API (FR-LAB-01, 02, 03; P2-05).

The priced synthetic tender's bill is estimated from the synthetic productivity list: its
pendent heads on their level, its pipework for the building, and a check valve the list
has no entry for. Conditions are proposed from bid parameters and confirmed by a person.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from firebid.db.models.audit import AuditEvent
from firebid.db.models.commercial import BoqLine
from firebid.db.models.core import Bid, Organisation
from firebid.db.models.labour import LabourProductivity
from firebid.domain.actors import Actor
from firebid.domain.state_machines import Role
from firebid.evals import synthetic_labour
from firebid.labour import estimate as est
from firebid.labour import rates
from firebid.services import costing, labour, qto
from tests.db.test_boq import line_for, people
from tests.db.test_pricing import priced  # noqa: F401
from tests.db.test_qto import tender  # noqa: F401
from tests.db.test_sheet_views import SignIn, app, member, sign_in  # noqa: F401
from tests.db.test_symbol_mapping import no_tiles, store  # noqa: F401
from tests.labour.test_labour import RATE_TABLES

pytestmark = pytest.mark.usefixtures("no_tiles")

TODAY = date(2026, 10, 3)
PENDENT = "Sprinkler, pendent"
CHECK_VALVE = "Check valve, DN150"


def library(session: Session, organisation: Organisation, senior: Actor) -> labour.ImportReport:
    report = labour.import_productivity(
        session,
        organisation.id,
        synthetic_labour.productivity_list(),
        senior,
        "productivity.xlsx",
    )
    session.commit()
    return report


def by_line(found: est.Estimate) -> dict[str, est.LabourLine]:
    return {item.line.id: item for item in found.lines}


@pytest.fixture
def staff(session: Session, organisation: Organisation, bid: Bid) -> tuple[Actor, Actor]:
    return people(session, organisation, bid)


@pytest.mark.req("FR-LAB-01")
class TestLibrary:
    def test_the_list_is_imported_and_every_entry_shows_its_source(
        self, session: Session, organisation: Organisation, staff: tuple[Actor, Actor]
    ) -> None:
        _, senior = staff

        report = library(session, organisation, senior)
        again = library(session, organisation, senior)

        assert report.imported and report.created == len(synthetic_labour.ENTRIES)
        assert (again.created, again.superseded, again.unchanged) == (0, 0, report.created)
        entries = [labour.entry_of(row) for row in labour.current_entries(session, organisation.id)]
        assert {entry.source for entry in entries} == {
            "company standard: PS-2026",
            "historical project: Tampines Hub (2025)",
            "estimator judgement: Sam Senior",
        }

    def test_a_changed_figure_is_a_new_version_and_the_old_one_is_kept(
        self, session: Session, organisation: Organisation, staff: tuple[Actor, Actor]
    ) -> None:
        _, senior = staff
        library(session, organisation, senior)
        changed = tuple(
            (*row[:5], 0.33, *row[6:]) if row[:2] == ("pipe", 50) else row
            for row in synthetic_labour.ENTRIES
        )

        report = labour.import_productivity(
            session,
            organisation.id,
            synthetic_labour.productivity_list(changed),
            senior,
            "productivity.xlsx",
        )
        session.commit()

        assert (report.created, report.superseded) == (0, 1)
        current = next(
            row
            for row in labour.current_entries(session, organisation.id)
            if (row.item_type, row.dn) == ("pipe", "50")
        )
        versions = labour.history(session, current)
        assert [(v.version, v.hours_per_unit, v.retired_at is None) for v in versions] == [
            (1, Decimal("0.3000"), False),
            (2, Decimal("0.3300"), True),
        ]

    def test_a_list_with_an_unsourced_entry_imports_nothing(
        self, session: Session, organisation: Organisation, staff: tuple[Actor, Actor]
    ) -> None:
        _, senior = staff
        bad = [("pipe", 65, "", "No source", "m", 0.4, "pipefitter", "company standard", "")]

        report = labour.import_productivity(
            session,
            organisation.id,
            synthetic_labour.productivity_list(extra=bad),
            senior,
            "bad.xlsx",
        )

        assert report.imported is False
        assert [(p["column"], p["message"]) for p in report.problems] == [
            ("source reference", "is empty")
        ]
        assert labour.current_entries(session, organisation.id) == []

    def test_the_database_refuses_an_entry_without_a_source(
        self, session: Session, organisation: Organisation
    ) -> None:
        session.add(
            LabourProductivity(
                organisation_id=organisation.id,
                item_type="pipe",
                description="Pipe",
                unit="m",
                hours_per_unit=Decimal("0.3"),
                trade="pipefitter",
                source_type="company_standard",
                source_reference="  ",
            )
        )
        with pytest.raises(IntegrityError, match="source_named"):
            session.flush()
        session.rollback()

    def test_an_estimator_s_judgement_is_entered_under_their_name(
        self, session: Session, organisation: Organisation, staff: tuple[Actor, Actor]
    ) -> None:
        _, senior = staff

        row = labour.set_entry(
            session,
            organisation.id,
            senior,
            item_type="check valve",
            dn="150",
            unit="nr",
            hours=Decimal("2.75"),
            trade="pipefitter",
            description="Check valve assembly DN150",
            source_type="estimator_judgement",
        )
        session.commit()

        assert (row.item_type, row.source_reference) == ("check_valve", senior.label)
        with pytest.raises(labour.LabourError, match="says which project"):
            labour.set_entry(
                session,
                organisation.id,
                senior,
                item_type="check valve",
                dn="100",
                unit="nr",
                hours=Decimal(2),
                trade="pipefitter",
                description="Check valve assembly DN100",
                source_type="historical_project",
            )


@pytest.fixture
def estimated(
    session: Session, priced: tuple[Bid, Actor, Actor], organisation: Organisation
) -> tuple[Bid, Actor, Actor]:
    bid, estimator, senior = priced
    library(session, organisation, senior)
    costing.set_priced_on(session, bid, TODAY, estimator)
    session.commit()
    return bid, estimator, senior


@pytest.mark.req("FR-LAB-01")
@pytest.mark.req("FR-LAB-02")
class TestEstimate:
    def test_hours_match_a_hand_calculation_with_every_factor_shown(
        self, session: Session, estimated: tuple[Bid, Actor, Actor]
    ) -> None:
        bid, estimator, senior = estimated
        heads = line_for(session, bid, PENDENT)
        valve = line_for(session, bid, CHECK_VALVE)
        assert heads.level is not None
        qto.set_parameter(
            session, bid.id, "ceiling_height_mm", Decimal(5200), estimator, level=heads.level
        )
        qto.set_parameter(session, bid.id, "levels_served", Decimal(24), estimator)

        proposed = labour.propose(session, bid, estimator)
        before = by_line(labour.estimate(session, bid, TODAY))

        assert [(row.level, row.multiplier_key, row.state) for row in proposed] == [
            (heads.level, "height_4_5m_to_6m", "proposed"),
            (None, "high_rise", "proposed"),
        ]
        assert "ceiling height 5200 mm" in proposed[0].basis
        # A proposal changes nothing until a person confirms it.
        assert before[str(heads.id)].multipliers == ()
        assert before[str(heads.id)].hours == before[str(heads.id)].baseline_hours

        labour.decide(
            session, bid, estimator, key="height_4_5m_to_6m", level=heads.level, state="confirmed"
        )
        labour.decide(session, bid, senior, key="high_rise", level=None, state="rejected")
        labour.decide(
            session,
            bid,
            senior,
            key="night_work",
            level=None,
            state="confirmed",
            basis="the mall trades by day: tender clause 1.4",
        )
        session.commit()
        found = labour.estimate(session, bid, TODAY)
        lines = by_line(found)

        # The heads, on their level: quantity x 0.40 h, x 1.25 (height) x 1.20 (night work).
        line = lines[str(heads.id)]
        assert line.entry is not None and line.entry.hours == Decimal("0.4000")
        assert line.entry.source == "company standard: PS-2026"
        assert line.baseline_hours == (heads.quantity * Decimal("0.40")).quantize(Decimal("0.01"))
        assert [(m.key, m.value, m.scope, m.confirmed_by) for m in line.multipliers] == [
            ("height_4_5m_to_6m", Decimal("1.25"), heads.level, estimator.label),
            ("night_work", Decimal("1.20"), "the whole bid", senior.label),
        ]
        assert all(m.source and m.rationale for m in line.multipliers)
        assert line.hours == (
            heads.quantity * Decimal("0.40") * Decimal("1.25") * Decimal("1.20")
        ).quantize(Decimal("0.01"))
        assert line.cost is not None and line.rate is not None
        assert line.rate.trade == "sprinkler_fitter"
        assert line.cost.amount == (line.hours * line.rate.hourly).quantize(Decimal("0.01"))

        # Pipework is billed for the building and worked level by level, from the takeoff
        # items behind each line. All of it here is on the heads' level, so it carries that
        # level's multiplier with the bid's.
        pipes = [
            item
            for item in found.lines
            if item.entry is not None and item.entry.type == "pipe" and item.line.level is None
        ]
        assert pipes and all(
            [m.key for m in item.multipliers] == ["height_4_5m_to_6m", "night_work"]
            for item in pipes
        )
        for item in pipes:
            assert item.entry is not None
            assert [part.level for part in item.portions] == [heads.level]
            assert item.portions[0].quantity == item.line.quantity
            assert item.hours == (
                item.line.quantity * item.entry.hours * Decimal("1.25") * Decimal("1.20")
            ).quantize(Decimal("0.01"))

        # No entry, no hours: listed, not guessed.
        assert lines[str(valve.id)].hours is None
        assert lines[str(valve.id)].reason == "no productivity entry"
        assert found.without_hours >= 1

        # Per system and by trade, adding up to the whole.
        costed = [item for item in found.lines if item.hours is not None]
        assert found.hours == sum((item.hours or Decimal(0) for item in costed), Decimal(0))
        assert sum((total.hours for total in found.by_section), Decimal(0)) == found.hours
        assert sum((total.cost for total in found.by_trade), Decimal(0)) == found.cost.amount
        assert {total.key for total in found.by_trade} == {"pipefitter", "sprinkler_fitter"}

    def test_a_multiplier_is_confirmed_by_a_named_person_with_a_reason(
        self, session: Session, estimated: tuple[Bid, Actor, Actor]
    ) -> None:
        bid, estimator, _ = estimated

        with pytest.raises(labour.LabourError, match="say why the multiplier applies"):
            labour.decide(session, bid, estimator, key="basement", level="B1", state="confirmed")
        with pytest.raises(labour.LabourError, match="a named person"):
            labour.decide(
                session, bid, Actor.system(), key="basement", level="B1", state="confirmed"
            )
        with pytest.raises(labour.LabourError, match="not a multiplier of the catalogue"):
            labour.decide(
                session, bid, estimator, key="dust", level=None, state="confirmed", basis="x"
            )
        row = labour.decide(
            session,
            bid,
            estimator,
            key="basement",
            level="B1",
            state="confirmed",
            basis="car park levels",
        )
        session.commit()

        event = session.execute(
            select(AuditEvent).where(AuditEvent.entity_id == str(row.id))
        ).scalar_one()
        assert event.action == "labour condition: confirmed" and event.reason == "car park levels"
        assert (row.decided_by, row.proposed_by) == (estimator.label, estimator.label)


@pytest.mark.req("FR-LAB-03")
class TestRates:
    def test_a_changed_table_value_changes_only_a_bid_priced_from_its_date(
        self, session: Session, estimated: tuple[Bid, Actor, Actor]
    ) -> None:
        bid, estimator, _ = estimated
        tables = rates.tables(RATE_TABLES)  # the levy rises on 1 July 2026
        pipe = next(
            line
            for line in session.execute(select(BoqLine).where(BoqLine.bid_id == bid.id)).scalars()
            if (line.item_key or "").startswith("pipe|50")
        )

        costing.set_priced_on(session, bid, date(2026, 6, 30), estimator)
        before = by_line(labour.estimate(session, bid, rate_tables=tables))[str(pipe.id)]
        costing.set_priced_on(session, bid, date(2026, 7, 1), estimator)
        after = by_line(labour.estimate(session, bid, rate_tables=tables))[str(pipe.id)]

        assert before.hours == after.hours
        assert before.rate is not None and after.rate is not None
        assert (before.rate.hourly, before.rate.effective_from) == (
            Decimal("19.7632"),
            date(2025, 1, 1),
        )
        assert (after.rate.hourly, after.rate.effective_from) == (
            Decimal("20.4844"),
            date(2026, 7, 1),
        )
        assert before.cost is not None and after.cost is not None and before.hours is not None
        assert before.cost.amount == (before.hours * Decimal("19.7632")).quantize(Decimal("0.01"))
        assert after.cost.amount == (before.hours * Decimal("20.4844")).quantize(Decimal("0.01"))

    def test_the_labour_line_of_the_cost_build_up_is_the_estimate_with_its_basis(
        self, session: Session, estimated: tuple[Bid, Actor, Actor]
    ) -> None:
        bid, estimator, _ = estimated
        found = labour.estimate(session, bid, TODAY)

        built = costing.build_up(session, bid, TODAY)

        line = next(item for item in built.lines if item.component == "labour")
        assert (line.basis, line.amount) == ("calculated", found.cost)
        assert f"{found.hours} man-hours" in line.detail
        assert "with no productivity entry are not included" in line.detail
        assert line.source == (
            "the productivity library and the labour rate table from 2025-01-01 "
            "(Company labour rate table 2025 (to be confirmed))"
        )
        assert "labour" not in built.not_set

        # An estimator's own figure stands, and says what the estimate came to.
        costing.enter(
            session, bid, estimator, component="labour", basis="lump_sum", amount=Decimal(20000)
        )
        entered = next(
            item
            for item in costing.build_up(session, bid, TODAY).lines
            if item.component == "labour"
        )
        assert entered.basis == "lump_sum"
        assert entered.source.endswith(f"(in place of the calculated {found.cost.amount})")


class TestApi:
    @pytest.mark.req("FR-LAB-01")
    def test_only_a_senior_estimator_changes_the_library_and_everyone_sees_its_sources(
        self, session: Session, bid: Bid, organisation: Organisation, sign_in: SignIn
    ) -> None:
        files = {
            "file": (
                "productivity.xlsx",
                synthetic_labour.productivity_list(),
                "application/octet-stream",
            )
        }
        estimator = sign_in(member(session, organisation, bid, "eve", Role.ESTIMATOR))
        refused = estimator.post("/labour/productivity/import", files=files)
        senior = sign_in(member(session, organisation, bid, "sid", Role.SENIOR_ESTIMATOR))

        imported = senior.post("/labour/productivity/import", files=files)
        added = senior.post(
            "/labour/productivity",
            json={
                "item_type": "check_valve",
                "dn": "150",
                "description": "Check valve assembly DN150",
                "unit": "nr",
                "hours_per_unit": "2.75",
                "trade": "pipefitter",
                "source_type": "estimator_judgement",
            },
        )
        unsourced = senior.post(
            "/labour/productivity",
            json={
                "item_type": "check_valve",
                "dn": "100",
                "description": "Check valve assembly DN100",
                "unit": "nr",
                "hours_per_unit": "2",
                "trade": "pipefitter",
                "source_type": "historical_project",
                "source_reference": " ",
            },
        )
        listed = senior.get("/labour/productivity").json()

        assert refused.status_code == 403
        assert imported.status_code == 200 and imported.json()["created"] == 8
        assert added.status_code == 201 and added.json()["source"] == "estimator judgement: Sid"
        assert unsourced.status_code == 422 and "says which project" in unsourced.json()["detail"]
        assert len(listed) == 9 and all(entry["source_reference"] for entry in listed)

    @pytest.mark.req("FR-LAB-02")
    @pytest.mark.req("FR-LAB-03")
    def test_the_estimate_shows_baseline_multipliers_and_the_rate_table_s_date(
        self,
        session: Session,
        estimated: tuple[Bid, Actor, Actor],
        organisation: Organisation,
        sign_in: SignIn,
    ) -> None:
        bid, _, _ = estimated
        heads = line_for(session, bid, PENDENT)
        client = sign_in(member(session, organisation, bid, "eve", Role.ESTIMATOR))

        session.execute(
            text(
                "INSERT INTO bid_parameter (id, bid_id, name, level, value, source) VALUES "
                "(gen_random_uuid(), :b, 'ceiling_height_mm', :l, 3600, 'entered by Eve')"
            ),
            {"b": bid.id, "l": heads.level},
        )
        session.commit()
        proposed = client.post(f"/bids/{bid.id}/labour/conditions/propose")
        confirmed = client.put(
            f"/bids/{bid.id}/labour/conditions",
            json={"key": "height_3m_to_4_5m", "level": heads.level, "state": "confirmed"},
        )
        unknown = client.put(
            f"/bids/{bid.id}/labour/conditions",
            json={"key": "dust", "state": "confirmed", "basis": "x"},
        )
        build_up = client.get(f"/bids/{bid.id}/cost/build-up").json()

        assert proposed.status_code == 200, proposed.text
        [condition] = proposed.json()["conditions"]
        assert (condition["multiplier_key"], condition["state"]) == (
            "height_3m_to_4_5m",
            "proposed",
        )
        assert condition["source"] and condition["rationale"]
        assert confirmed.status_code == 200, confirmed.text
        assert unknown.status_code == 422
        body = confirmed.json()
        line = next(item for item in body["lines"] if item["line_id"] == str(heads.id))
        assert Decimal(line["baseline_hours"]) == (heads.quantity * Decimal("0.40")).quantize(
            Decimal("0.01")
        )
        [applied] = line["multipliers"]
        assert (applied["key"], Decimal(applied["value"]), applied["confirmed_by"]) == (
            "height_3m_to_4_5m",
            Decimal("1.10"),
            "Eve",
        )
        assert Decimal(line["hours"]) == (
            heads.quantity * Decimal("0.40") * Decimal("1.10")
        ).quantize(Decimal("0.01"))
        assert line["productivity_source"] == "company standard: PS-2026"
        assert body["priced_on"] == "2026-10-03"
        catalogue = body["catalogue"]
        assert catalogue["rate_table_effective_from"] == "2025-01-01"
        fitter = next(t for t in catalogue["trades"] if t["trade"] == "sprinkler_fitter")
        assert Decimal(line["hourly_rate"]) == Decimal(fitter["hourly"])
        assert [c["key"] for c in fitter["grades"][0]["components"]] == [
            "wage",
            "levy",
            "accommodation",
            "transport",
            "insurance",
            "overtime",
            "supervision",
        ]
        labour_line = next(item for item in build_up["lines"] if item["component"] == "labour")
        assert labour_line["basis"] == "calculated"
        assert Decimal(labour_line["amount"]) == Decimal(body["cost"])
