# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""The Phase 2 systems through the database: from uploaded sheets to QTO items (P2-01).

The synthetic tender of `synthetic_systems` goes through the parse pipeline as uploaded
drawings do. A person confirms the legend, detection runs, then takeoff, which reads the
pump schedule and the level schedule from the sheets' stored text.

Also here: an organisation that had its library and rules before this step gains the new
types and rules, and nothing it already had is touched.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from firebid.db.models.core import Bid, Organisation
from firebid.db.models.symbols import ObjectType
from firebid.db.models.takeoff import DetectedObject, MeasurementRule, PipeRun, QtoItem
from firebid.domain.actors import Actor
from firebid.evals import synthetic_systems as fixture
from firebid.qto import rules
from firebid.services import object_library as library
from firebid.services import qto
from firebid.services.detection import detect_bid
from firebid.storage.object_store import MemoryObjectStore
from tests.db.test_detection_pipeline import confirm_legend
from tests.db.test_symbol_mapping import from_consultant, no_tiles, read, store  # noqa: F401

DUTY_PUMP = "Fire pump (duty, 2850 L/min, 80 m head, 75 kW)"
SENIOR = Actor(label="Sam Lee", roles=frozenset({"senior_estimator"}))


@pytest.fixture
def tender(session: Session, bid: Bid, store: MemoryObjectStore) -> Iterator[Bid]:
    from_consultant(session, bid, fixture.DELTA.name)
    for document, truth in fixture.tender():
        read(session, bid, store, truth.number, document)
    confirm_legend(session, bid, store, fixture.DESCRIBED)
    detect_bid(session, store, bid.id)
    qto.recompute(session, bid.id)
    session.execute(text("DELETE FROM procrastinate_jobs"))
    session.commit()
    yield bid


def items(session: Session, bid: Bid) -> list[QtoItem]:
    return qto.live_items(session, bid.id)


def one(session: Session, bid: Bid, description: str) -> QtoItem:
    [found] = [item for item in items(session, bid) if item.description == description]
    return found


@pytest.mark.req("FR-VIS-04")
class TestDetections:
    def test_equipment_is_stored_with_its_tag_method_and_evidence(
        self, session: Session, tender: Bid
    ) -> None:
        stored = list(
            session.execute(
                select(DetectedObject).where(
                    DetectedObject.bid_id == tender.id, DetectedObject.object_type == "fire_pump"
                )
            ).scalars()
        )

        # Two on the pump room plan, and the same two again on the schematic.
        assert sorted(str(row.attributes["tag"]) for row in stored) == [
            "FP-01",
            "FP-01",
            "FP-02",
            "FP-02",
        ]
        for row in stored:
            assert row.extraction_method == "cad_block" and row.state == "proposed"
            assert row.source_ref["tag"]["text"] == row.attributes["tag"]  # type: ignore[index]
            assert row.confidence is not None and row.detector_version == "2"

    def test_runs_are_stored_with_the_system_their_pipework_shows(
        self, session: Session, tender: Bid
    ) -> None:
        systems = {
            str(run.features.get("system"))
            for run in session.execute(select(PipeRun).where(PipeRun.bid_id == tender.id)).scalars()
        }

        assert systems == {"wet_riser", "hydrant", "rising_main"}


@pytest.mark.req("FR-QTO-06")
class TestTakeoff:
    def test_equipment_is_taken_off_once_with_counts_and_attributes(
        self, session: Session, tender: Bid
    ) -> None:
        counted = {
            item.description: item.net_quantity
            for item in items(session, tender)
            if item.classification in ("equipment", "valve")
        }

        assert counted == {
            DUTY_PUMP: Decimal(1),
            "Fire pump (standby, 2850 L/min, 80 m head, 75 kW)": Decimal(1),
            "Jockey pump (90 L/min, 85 m head, 4 kW)": Decimal(1),
            "Pump controller": Decimal(1),
            "Fire water tank": Decimal(1),
            "Test header": Decimal(1),
            "Air compressor": Decimal(1),
            "Dry pipe valve set": Decimal(1),
            "Pre action valve set": Decimal(1),
            "Deluge valve set": Decimal(1),
            "Landing valve (DN100)": Decimal(1),
            "Hose reel": Decimal(2),
            "Hydrant": Decimal(3),
            "Breeching inlet": Decimal(1),
        }

    def test_a_pump_s_duty_cites_the_schedule_row_on_its_sheet(
        self, session: Session, tender: Bid
    ) -> None:
        pump = one(session, tender, DUTY_PUMP)

        flow = pump.attributes["flow_l_min"]
        assert (flow["value"], flow["source"]) == ("2850", "schedule")  # type: ignore[index]
        [cited] = flow["citations"]  # type: ignore[index]
        assert cited["sheet_number"] == fixture.PUMP_ROOM
        assert cited["quote"] == "FP-01 | ELECTRIC FIRE PUMP | DUTY | 47.5 | 80 | 75"
        assert pump.state == "proposed" and pump.level == "B1"

    def test_every_equipment_item_has_a_complete_evidence_record(
        self, session: Session, tender: Bid
    ) -> None:
        assert qto.completeness(session, tender.id) == []

    def test_the_rising_main_uses_the_level_schedule_and_says_so(
        self, session: Session, tender: Bid
    ) -> None:
        riser = one(session, tender, "Rising main, DN100 (vertical, not drawn)")

        assert riser.length is not None and riser.length.mm == fixture.FLOOR_TO_FLOOR_L03
        inputs = riser.derivation["rule"]["inputs"]  # type: ignore[index]
        [height] = [i for i in inputs if i["name"] == "floor_to_floor_mm"]
        assert height["source"].startswith(f"level schedule on sheet {fixture.SCHEMATIC}")

    def test_the_hydrant_main_is_measured_from_the_site_plan(
        self, session: Session, tender: Bid
    ) -> None:
        main = one(session, tender, "Pipe, DN150, main, hydrant system")

        assert main.length is not None and main.length.mm == 26_250
        assert main.calculation_method == "centreline_length"

    def test_taking_off_again_changes_nothing(self, session: Session, tender: Bid) -> None:
        before = {item.id for item in items(session, tender)}

        outcome = qto.recompute(session, tender.id)

        assert (outcome.created, outcome.superseded) == (0, 0)
        assert {item.id for item in items(session, tender)} == before


@pytest.mark.req("FR-QTO-07")
class TestHangers:
    def test_with_no_specification_hangers_cite_the_company_default(
        self, session: Session, tender: Bid
    ) -> None:
        hangers = [item for item in items(session, tender) if item.item_type == "pipe_hanger"]

        assert hangers, "pump room and floor pipework is hung"
        for item in hangers:
            assert item.rule_key == "hanger_spacing" and item.rule_version == 1
            source = item.attributes["spacing_mm"]["source"]  # type: ignore[index]
            assert source == "company default (rule hanger_spacing v1, to be confirmed)"
            assert item.calculation_method == "rule_derived"
        # The buried hydrant main is not hung, and nothing is braced: no clause requires it.
        assert all(item.level in ("B1", "L03") for item in hangers)
        assert not [i for i in items(session, tender) if i.item_type.startswith("seismic")]

    def test_a_confirmed_spacing_is_a_new_rule_version_on_the_items(
        self, session: Session, organisation: Organisation, tender: Bid
    ) -> None:
        qto.edit_rule(
            session,
            organisation.id,
            "hanger_spacing",
            definition={"default_spacing_mm": [{"up_to_dn": 999, "spacing_mm": 2000}]},
            actor=SENIOR,
            note="company practice",
        )

        qto.recompute(session, tender.id)

        dn100 = one(session, tender, "Pipe hanger, DN100 (rule-derived: not drawn)")
        assert dn100.rule_version == 2 and dn100.net_quantity == Decimal(2)  # 2,550 / 2,000


class TestAnOrganisationFromBeforeThisStep:
    @pytest.mark.req("FR-VIS-04")
    def test_its_library_gains_the_new_types_and_keeps_its_own(
        self, session: Session, organisation: Organisation, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        everything = library.seed_types()
        phase_1 = [spec for spec in everything if spec.category != "equipment"]
        monkeypatch.setattr(library, "seed_types", lambda: phase_1)
        library.ensure_seeded(session, organisation.id)
        library.change(
            session,
            organisation.id,
            "gate_valve",
            SENIOR,
            label="Gate valve (OS&Y)",
            note="company wording",
        )
        monkeypatch.setattr(library, "seed_types", lambda: everything)

        library.ensure_seeded(session, organisation.id)
        library.ensure_seeded(session, organisation.id)

        current = {item.key: item for item in library.current(session, organisation.id)}
        assert current["hydrant"].version == 1 and current["fire_pump"].category == "equipment"
        assert (current["gate_valve"].version, current["gate_valve"].label) == (
            2,
            "Gate valve (OS&Y)",
        )
        rows = session.execute(
            select(ObjectType).where(
                ObjectType.organisation_id == organisation.id, ObjectType.key == "hydrant"
            )
        ).scalars()
        assert len(list(rows)) == 1

    @pytest.mark.req("FR-QTO-07")
    def test_its_rules_gain_hangers_and_seismic_restraint_and_keep_its_own(
        self, session: Session, organisation: Organisation, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        everything = rules.seed_rules()
        phase_1 = [r for r in everything if r.key not in ("hanger_spacing", "seismic_restraint")]
        monkeypatch.setattr(rules, "seed_rules", lambda: phase_1)
        qto.rule_rows(session, organisation.id)
        qto.edit_rule(
            session,
            organisation.id,
            "riser_length",
            definition={"defaults": {"floor_to_floor_mm": 3600, "levels_served": 1}},
            actor=SENIOR,
        )
        monkeypatch.setattr(rules, "seed_rules", lambda: everything)

        in_force = qto.rule_rows(session, organisation.id)
        again = qto.rule_rows(session, organisation.id)

        assert in_force["hanger_spacing"].version == 1
        assert in_force["seismic_restraint"].status == "to be confirmed"
        assert in_force["riser_length"].version == 2
        assert {k: v.id for k, v in again.items()} == {k: v.id for k, v in in_force.items()}
        rows = session.execute(
            select(MeasurementRule).where(
                MeasurementRule.organisation_id == organisation.id,
                MeasurementRule.key == "hanger_spacing",
            )
        ).scalars()
        assert len(list(rows)) == 1
