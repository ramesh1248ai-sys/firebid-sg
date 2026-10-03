# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""Takeoff through the database and the API: from detections on Current sheets to QTO items.

A tender of three sheets goes through the parse pipeline as uploaded drawings do: the
general arrangement (with the legend and a ceiling-height note), an enlarged plan of the
riser area at 1:50 (its scale unverified: it has nothing to check it against), and a riser
schematic. A person confirms the legend, detection runs, then takeoff. Every quantity is
the installation's, once, with its evidence record.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from firebid.auth.provisioning import Principal
from firebid.db.models.core import Bid, Organisation
from firebid.db.models.documents import SheetRevision
from firebid.db.models.drawings import SheetView
from firebid.db.models.takeoff import DuplicateGroup, Evidence, QtoItem
from firebid.domain.actors import Actor
from firebid.domain.state_machines import Role, SheetRevisionState
from firebid.evals import synthetic_qto as fixture
from firebid.evals.synthetic_network import NETWORK
from firebid.services import qto
from firebid.services.detection import detect_bid
from firebid.services.transitions import apply_transition
from firebid.storage.object_store import MemoryObjectStore
from tests.db.test_detection_pipeline import confirm_legend
from tests.db.test_sheet_views import SignIn, app, member, sign_in  # noqa: F401
from tests.db.test_symbol_mapping import from_consultant, no_tiles, read, store  # noqa: F401

PENDENT = "Sprinkler, pendent"
DROPS = "Sprinkler drop, DN25 (vertical, not drawn)"
BRANCH = "Pipe, DN50, branch"
NOTE_SOURCE = "sheet FP-L05-201 note 'CEILING HEIGHT 2750'"


@pytest.fixture
def tender(session: Session, bid: Bid, store: MemoryObjectStore) -> Iterator[Bid]:
    from_consultant(session, bid, NETWORK.name)
    general, _ = fixture.general_arrangement()
    enlarged, _ = fixture.enlarged_plan()
    schematic, _ = fixture.riser_schematic()
    read(session, bid, store, "FP-L05-201", general)
    read(session, bid, store, "FP-L05-301", enlarged)
    read(session, bid, store, "FP-SCH-001", schematic)
    confirm_legend(session, bid, store)
    detect_bid(session, store, bid.id)
    qto.recompute(session, bid.id)
    # `read` does what the parse job does; the jobs uploading queued have nothing left to do.
    session.execute(text("DELETE FROM procrastinate_jobs"))
    session.commit()
    yield bid


def items(session: Session, bid: Bid) -> dict[str, QtoItem]:
    return {item.description: item for item in qto.live_items(session, bid.id)}


def api_items(client: TestClient, bid: Bid) -> dict[str, dict]:  # type: ignore[type-arg]
    response = client.get(f"/bids/{bid.id}/qto/items")
    assert response.status_code == 200, response.text
    return {item["description"]: item for item in response.json()}


def estimator(session: Session, organisation: Organisation, bid: Bid) -> tuple[Actor, Principal]:
    principal = member(session, organisation, bid, "esther", Role.ESTIMATOR)
    return principal.actor(), principal


@pytest.mark.req("FR-QTO-01")
@pytest.mark.req("FR-QTO-02")
@pytest.mark.req("FR-QTO-05")
class TestTakeoff:
    def test_counts_and_lengths_are_the_installation_s_once(
        self, session: Session, tender: Bid
    ) -> None:
        found = items(session, tender)

        # The enlarged plan repeats the riser area, and the schematic the valves: once each.
        assert {k: v.net_quantity for k, v in found.items() if v.unit == "no"} == {
            PENDENT: Decimal(16),
            "Sprinkler, upright": Decimal(4),
            "Sprinkler, sidewall": Decimal(4),
            "Gate valve, DN150": Decimal(1),
            "Check valve, DN150": Decimal(1),
            "Fitting, DN150 (fitting reducer)": Decimal(1),
            "Tee, DN150xDN50 (rule-derived: not drawn)": Decimal(3),
            "Tee, DN100xDN50 (rule-derived: not drawn)": Decimal(3),
            # Hangers by rule (P2-01), at the company default spacing: no specification here.
            "Pipe hanger, DN50 (rule-derived: not drawn)": Decimal(24),
            "Pipe hanger, DN100 (rule-derived: not drawn)": Decimal(3),
            "Pipe hanger, DN150 (rule-derived: not drawn)": Decimal(2),
        }
        lengths = {k: v.length.mm for k, v in found.items() if v.length is not None}
        assert lengths["Pipe, DN150, main"] == 8_050
        assert lengths["Pipe, DN100, main"] == 8_250
        assert lengths[BRANCH] == 72_000
        assert {item.state for item in found.values()} == {"proposed"}

    def test_attributes_say_where_they_came_from(self, session: Session, tender: Bid) -> None:
        found = items(session, tender)

        # No specification in this tender: nothing is guessed.
        assert found[PENDENT].attributes["finish"] == {
            "value": "not specified",
            "source": "not specified",
        }
        assert found["Gate valve, DN150"].attributes["nominal_diameter_mm"] == {
            "value": "150",
            "source": "drawing",
        }
        assert found[PENDENT].level == "L05"

    def test_only_current_sheets_feed_takeoff(self, session: Session, tender: Bid) -> None:
        schematic = session.execute(
            select(SheetRevision).where(SheetRevision.sheet_number == "FP-SCH-001")
        ).scalar_one()
        apply_transition(
            session,
            schematic,
            target=SheetRevisionState.WITHDRAWN,
            actor=Actor(label="Esther Tan", roles=frozenset({"estimator"})),
            reason="not part of this package",
        )

        qto.recompute(session, tender.id)

        kinds = {g.kind for g in session.execute(select(DuplicateGroup)).scalars()}
        assert kinds == {"enlarged_plan"}


@pytest.mark.req("FR-QTO-03")
@pytest.mark.req("FR-QTO-04")
class TestRuleDerivedInTheApi:
    def test_rule_id_version_inputs_and_sources_are_shown(
        self, session: Session, organisation: Organisation, tender: Bid, sign_in: SignIn
    ) -> None:
        _, principal = estimator(session, organisation, tender)
        found = api_items(sign_in(principal), tender)

        drops = found[DROPS]
        assert drops["rule_derived"] is True
        assert drops["net_quantity"] == "12.000"  # 24 heads x (3,300 - 2,750 - 50)
        rule = drops["rule"]
        assert (rule["rule_key"], rule["rule_version"]) == ("drop_length", 1)
        assert rule["rule_status"] == "to be confirmed"
        inputs = {i["name"]: i for i in rule["inputs"]}
        assert inputs["ceiling_height_mm"]["source"] == NOTE_SOURCE
        assert inputs["ceiling_height_mm"]["value"] == 2750
        assert inputs["branch_elevation_mm"]["source"] == "rule default (to be confirmed)"
        riser = found["Riser, DN150 (vertical, not drawn)"]
        assert riser["rule"]["rule_key"] == "riser_length" and riser["net_quantity"] == "4.000"
        tee = found["Tee, DN150xDN50 (rule-derived: not drawn)"]
        assert tee["rule"]["rule_key"] == "fitting_tee" and tee["rule"]["inputs"]

    def test_an_entered_height_replaces_the_note_and_says_who(
        self, session: Session, organisation: Organisation, tender: Bid, sign_in: SignIn
    ) -> None:
        _, principal = estimator(session, organisation, tender)
        client = sign_in(principal)

        response = client.post(
            f"/bids/{tender.id}/qto/parameters",
            json={"name": "ceiling_height_mm", "value": "2600", "level": "L05"},
        )
        assert response.status_code == 201, response.text
        queued = session.execute(
            text("SELECT count(*) FROM procrastinate_jobs WHERE task_name = 'qto.recompute'")
        ).scalar_one()
        assert queued == 1
        qto.recompute(session, tender.id)
        session.commit()

        drops = api_items(client, tender)[DROPS]
        assert drops["net_quantity"] == str(Decimal(24 * (3300 - 2600 - 50)) / 1000) + "00"
        inputs = {i["name"]: i for i in drops["rule"]["inputs"]}
        assert inputs["ceiling_height_mm"]["source"] == "entered by Esther"
        listed = client.get(f"/bids/{tender.id}/qto/parameters").json()
        assert [(p["source"], p["entered"]) for p in listed] == [
            (NOTE_SOURCE, False),
            ("entered by Esther", True),
        ]


@pytest.mark.req("FR-QTO-08")
class TestDuplicatesAndG1:
    def test_each_group_shows_every_evidence_location(
        self, session: Session, organisation: Organisation, tender: Bid, sign_in: SignIn
    ) -> None:
        _, principal = estimator(session, organisation, tender)

        groups = sign_in(principal).get(f"/bids/{tender.id}/qto/duplicates").json()

        assert [(g["kind"], g["status"]) for g in groups] == [
            ("enlarged_plan", "unresolved"),
            ("schematic", "auto_excluded"),
        ]
        enlarged = groups[0]["members"]
        assert {m["sheet_number"] for m in enlarged} == {"FP-L05-201", "FP-L05-301"}
        assert all(m["grid_reference"] and "x" in m for m in enlarged if m["kind"] == "detection")
        gate_valves = [m for m in enlarged if m.get("object_type") == "gate_valve"]
        assert sorted(m["keep"] for m in gate_valves) == [False, True]
        # The items it touches point at it, for the workbench.
        found = items(session, tender)
        assert found["Gate valve, DN150"].duplicate_group_id is not None
        assert found["Sprinkler, upright"].duplicate_group_id is None

    def test_g1_is_refused_while_a_group_is_unresolved(
        self, session: Session, organisation: Organisation, tender: Bid, sign_in: SignIn
    ) -> None:
        senior = member(session, organisation, tender, "sam", Role.SENIOR_ESTIMATOR)
        client = sign_in(senior)

        status_before = client.get(f"/bids/{tender.id}/qto/g1").json()
        refused = client.post(f"/bids/{tender.id}/qto/g1/approve", json={})
        group = session.execute(
            select(DuplicateGroup).where(DuplicateGroup.status == "unresolved")
        ).scalar_one()
        decided = client.post(
            f"/bids/{tender.id}/qto/duplicates/{group.id}",
            json={"decision": "confirmed", "note": "the enlarged plan repeats the GA"},
        )
        # G1 also needs every item verified and every symbol named (P1-08, FR-REV-04).
        from firebid.services import review, review_actions, symbols

        for unlisted in review.unmapped_in_scope(session, tender.id):
            symbols.name_unlisted(
                session, tender.id, unlisted["symbol_key"], "not_an_object", senior.actor()
            )
        review_actions.accept(
            session, tender.id, [i.id for i in qto.live_items(session, tender.id)], senior.actor()
        )
        session.commit()
        approved = client.post(f"/bids/{tender.id}/qto/g1/approve", json={"comment": "ok"})

        assert status_before["clear"] is False and len(status_before["unresolved_groups"]) == 1
        assert refused.status_code == 409 and "duplicate" in refused.json()["detail"]
        assert decided.status_code == 200 and decided.json()["decided_by"] == "Sam"
        assert approved.status_code == 201, approved.text
        assert approved.json()["gate"] == "G1" and approved.json()["snapshot_hash"]

    def test_g1_waits_for_drawings_still_being_read(
        self, session: Session, organisation: Organisation, tender: Bid
    ) -> None:
        """Approving while detection is queued would approve a takeoff about to change."""
        from firebid.jobs.enqueue import enqueue
        from firebid.jobs.tasks import run_detection

        actor, _ = estimator(session, organisation, tender)
        group = session.execute(
            select(DuplicateGroup).where(DuplicateGroup.status == "unresolved")
        ).scalar_one()
        qto.decide_group(session, group, "confirmed", actor)
        enqueue(session, run_detection, bid_id=str(tender.id), user_id="")

        blockers = qto.g1_blockers(session, tender.id)

        assert blockers.pending_work == [{"task": "detection.run", "status": "todo", "jobs": 1}]
        with pytest.raises(qto.QtoError, match="still to finish"):
            qto.approve_g1(session, tender, actor, "senior_estimator")

    def test_approving_g1_recomputes_first(
        self, session: Session, organisation: Organisation, tender: Bid
    ) -> None:
        """A group that only a recompute would find still blocks the approval."""
        actor, _ = estimator(session, organisation, tender)
        session.execute(text("DELETE FROM duplicate_group"))
        session.flush()

        with pytest.raises(qto.QtoError, match="unresolved duplicate"):
            qto.approve_g1(session, tender, actor, "senior_estimator")

    def test_only_a_senior_estimator_approves_g1(
        self, session: Session, organisation: Organisation, tender: Bid, sign_in: SignIn
    ) -> None:
        _, principal = estimator(session, organisation, tender)

        response = sign_in(principal).post(f"/bids/{tender.id}/qto/g1/approve", json={})

        assert response.status_code == 403

    def test_a_group_found_again_keeps_the_person_s_decision(
        self, session: Session, organisation: Organisation, tender: Bid
    ) -> None:
        actor, _ = estimator(session, organisation, tender)
        group = session.execute(
            select(DuplicateGroup).where(DuplicateGroup.status == "unresolved")
        ).scalar_one()
        qto.decide_group(session, group, "not_duplicate", actor)

        qto.recompute(session, tender.id)

        again = session.execute(select(DuplicateGroup).where(DuplicateGroup.id == group.id))
        assert again.scalar_one().status == "not_duplicate"
        assert items(session, tender)["Gate valve, DN150"].net_quantity == 2


@pytest.mark.req("FR-QTO-09")
class TestEvidence:
    def test_every_generated_item_has_a_complete_record(
        self, session: Session, tender: Bid
    ) -> None:
        live = qto.live_items(session, tender.id)
        records = {
            row.qto_item_id: row
            for row in session.execute(
                select(Evidence).where(Evidence.bid_id == tender.id)
            ).scalars()
        }

        assert live and {item.id for item in live} == set(records)
        assert all(row.missing_fields == [] for row in records.values())
        branch = next(item for item in live if item.description == BRANCH)
        record: dict[str, Any] = dict(records[branch.id].record)
        assert record["source"]["sheet_number"] == "FP-L05-201"
        assert record["source"]["revision_label"] == "R01"
        assert record["detection_method"] == "cad_entity"
        assert record["calculation_method"] == "centreline_length"
        assert record["location"]["level"] == "L05"
        assert record["evidence_links"] and record["geometry_reference"]
        assert "allowance 5" in record["quantity_note"]

    def test_an_incomplete_item_is_flagged_and_blocks_g1(
        self, session: Session, organisation: Organisation, tender: Bid
    ) -> None:
        actor, _ = estimator(session, organisation, tender)
        group = session.execute(
            select(DuplicateGroup).where(DuplicateGroup.status == "unresolved")
        ).scalar_one()
        qto.decide_group(session, group, "confirmed", actor)
        # A count with nothing to show where it is: no sheet, no level, no geometry.
        loose = qto.create_manual(
            session,
            tender.id,
            actor,
            item_type="hose_reel",
            description="Hose reel",
            unit="no",
            quantity=Decimal(2),
        )

        blockers = qto.g1_blockers(session, tender.id)

        assert [i["human_id"] for i in blockers.incomplete_items] == [loose.human_id]
        assert set(blockers.incomplete_items[0]["missing"]) >= {
            "geometry_reference",
            "evidence_links",
            "source.sheet",
            "location.level",
        }
        with pytest.raises(qto.QtoError, match="incomplete evidence"):
            qto.approve_g1(session, tender, actor, "senior_estimator")


@pytest.mark.req("FR-QTO-10")
class TestNetAndAllowance:
    def test_net_and_allowance_are_separate_in_the_api_and_the_export(
        self, session: Session, organisation: Organisation, tender: Bid, sign_in: SignIn
    ) -> None:
        _, principal = estimator(session, organisation, tender)
        client = sign_in(principal)

        branch = api_items(client, tender)[BRANCH]
        exported = client.get(f"/bids/{tender.id}/qto/export.csv")

        assert (branch["net_quantity"], branch["allowance_percent"]) == ("72.000", "5.000")
        assert branch["allowance_quantity"] == "3.600"
        assert branch["quantity_with_allowance"] == "75.600"
        assert exported.status_code == 200
        assert "attachment" in exported.headers["content-disposition"]
        rows = {row["Description"]: row for row in csv.DictReader(io.StringIO(exported.text))}
        assert rows[BRANCH]["Net quantity"] == "72.000"
        assert rows[BRANCH]["Allowance %"] == "5.000"
        assert rows[BRANCH]["Allowance quantity"] == "3.600"
        assert rows[BRANCH]["Quantity with allowance"] == "75.600"
        assert rows[DROPS]["Rule"] == "drop_length v1"


class TestRecompute:
    def test_the_same_inputs_change_nothing(self, session: Session, tender: Bid) -> None:
        before = qto.snapshot_hash(qto.live_items(session, tender.id))

        outcome = qto.recompute(session, tender.id)

        assert (outcome.created, outcome.superseded) == (0, 0)
        assert outcome.unchanged == len(qto.live_items(session, tender.id))
        assert qto.snapshot_hash(qto.live_items(session, tender.id)) == before

    def test_verification_survives_on_items_whose_inputs_did_not_change(
        self, session: Session, organisation: Organisation, tender: Bid
    ) -> None:
        actor, _ = estimator(session, organisation, tender)
        pendent = items(session, tender)[PENDENT]
        qto.verify(session, pendent, actor)
        qto.set_parameter(
            session, tender.id, "ceiling_height_mm", Decimal(2600), actor, level="L05"
        )

        outcome = qto.recompute(session, tender.id)

        after = items(session, tender)
        assert (outcome.created, outcome.superseded) == (1, 1)
        assert after[PENDENT].id == pendent.id and after[PENDENT].state == "verified"
        drops = after[DROPS]
        assert drops.version == 2 and drops.state == "proposed"
        old = session.get(QtoItem, (drops.supersedes_id, tender.id))
        assert old is not None and old.state == "superseded"
        assert old.human_id == drops.human_id and old.net_quantity == Decimal("12.000")


@pytest.mark.req("NFR-01")
class TestRecomputeAtScale:
    """Found on a real tender of 121 sheets: what must stay true as a bid grows."""

    def test_an_unchanged_recompute_writes_nothing_and_asks_once_not_once_an_item(
        self, session: Session, tender: Bid
    ) -> None:
        from sqlalchemy import event

        qto.recompute(session, tender.id)
        session.commit()
        statements: list[str] = []

        def record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
            statements.append(statement)

        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", record)
        try:
            outcome = qto.recompute(session, tender.id)
            session.flush()
        finally:
            event.remove(engine, "before_cursor_execute", record)

        assert (outcome.created, outcome.superseded) == (0, 0)
        writes = [s for s in statements if s.lstrip().upper().startswith(("UPDATE", "INSERT"))]
        # Nothing about the takeoff or its evidence is rewritten when nothing changed.
        assert not [s for s in writes if "qto_item" in s or "evidence" in s], writes[:3]
        # The project and the people who verified are read once, however many items.
        assert sum("FROM project" in s for s in statements) <= 1
        assert sum("FROM app_user" in s for s in statements) <= 1
        assert sum("FROM qto_item" in s for s in statements) <= 2

    def test_an_item_stored_twice_under_one_key_is_retired_and_stays_retired(
        self, session: Session, tender: Bid
    ) -> None:
        pendent = items(session, tender)[PENDENT]
        # What an earlier recompute left behind when two items shared a key: a second live
        # item with the first one's key.
        session.execute(
            text(
                "INSERT INTO qto_item (id, bid_id, human_id, item_type, classification, "
                "description, attributes, unit, net_quantity, calculation_method, is_manual, "
                "confidence, state, version, item_key, inputs_hash, derivation, created_at) "
                "SELECT gen_random_uuid(), bid_id, 'QTO-999999', item_type, classification, "
                "description, attributes, unit, net_quantity, calculation_method, is_manual, "
                "confidence, state, version, item_key, inputs_hash, derivation, "
                "created_at - interval '1 hour' FROM qto_item WHERE id = :id"
            ),
            {"id": pendent.id},
        )
        session.commit()
        assert (
            len([i for i in qto.live_items(session, tender.id) if i.item_key == pendent.item_key])
            == 2
        )

        first = qto.recompute(session, tender.id)
        session.commit()
        second = qto.recompute(session, tender.id)

        assert (first.created, first.superseded) == (0, 1)
        assert (second.created, second.superseded) == (0, 0)
        live = [i for i in qto.live_items(session, tender.id) if i.item_key == pendent.item_key]
        # The newer of the two is kept: the one the takeoff was last made as.
        assert [i.id for i in live] == [pendent.id]


@pytest.mark.req("FR-QTO-11")
class TestManualItems:
    def _views(self, session: Session) -> dict[str, SheetView]:
        return {v.kind: v for v in session.execute(select(SheetView)).scalars()}

    def test_a_count_and_a_measured_length_tagged_with_who_and_when(
        self, session: Session, organisation: Organisation, tender: Bid, sign_in: SignIn
    ) -> None:
        _, principal = estimator(session, organisation, tender)
        client = sign_in(principal)
        plan = self._views(session)["plan"]

        counted = client.post(
            f"/bids/{tender.id}/qto/items",
            json={
                "item_type": "flow_switch",
                "description": "Flow switch",
                "quantity": "1",
                "level": "L05",
            },
        )
        measured = client.post(
            f"/bids/{tender.id}/qto/items",
            json={
                "item_type": "pipe",
                "description": "Pipe, DN65, drain",
                "measure": {"view_id": str(plan.id), "points": [[100, 100], [160, 100]]},
            },
        )

        assert counted.status_code == 201, counted.text
        assert counted.json()["manual"] is True
        assert counted.json()["manual_by"] == "Esther" and counted.json()["manual_at"]
        assert counted.json()["state"] == "proposed"
        assert measured.status_code == 201, measured.text
        body = measured.json()
        # 60 mm on paper at the view's verified 1:100.
        assert (body["length_mm"], body["net_quantity"], body["unit"]) == (6000, "6.000", "m")
        assert body["calculation_method"] == "manual_measure" and body["level"] == "L05"
        assert body["evidence_missing"] == []

    def test_measuring_is_refused_on_a_view_whose_scale_is_not_verified(
        self, session: Session, organisation: Organisation, tender: Bid, sign_in: SignIn
    ) -> None:
        _, principal = estimator(session, organisation, tender)
        enlarged = self._views(session)["enlarged plan"]
        assert enlarged.scale_status not in ("verified", "calibrated")

        response = sign_in(principal).post(
            f"/bids/{tender.id}/qto/items",
            json={
                "item_type": "pipe",
                "description": "Pipe, DN65, drain",
                "measure": {"view_id": str(enlarged.id), "points": [[100, 100], [160, 100]]},
            },
        )

        assert response.status_code == 409
        assert "cannot be measured" in response.json()["detail"]

    def test_edit_is_a_new_version_and_delete_is_a_rejection(
        self, session: Session, organisation: Organisation, tender: Bid, sign_in: SignIn
    ) -> None:
        _, principal = estimator(session, organisation, tender)
        client = sign_in(principal)
        created = client.post(
            f"/bids/{tender.id}/qto/items",
            json={"item_type": "flow_switch", "description": "Flow switch", "quantity": "1"},
        ).json()

        edited = client.patch(
            f"/bids/{tender.id}/qto/items/{created['id']}", json={"quantity": "2"}
        )
        deleted = client.delete(f"/bids/{tender.id}/qto/items/{edited.json()['id']}")
        history = client.get(f"/bids/{tender.id}/qto/items/{created['id']}/history").json()

        assert edited.status_code == 200, edited.text
        assert edited.json()["human_id"] == created["human_id"]
        assert (edited.json()["version"], edited.json()["net_quantity"]) == (2, "2.000")
        assert deleted.status_code == 204
        assert [(v["version"], v["state"]) for v in history] == [
            (1, "superseded"),
            (2, "rejected"),
        ]

    def test_generated_items_are_not_edited_as_manual_ones(
        self, session: Session, organisation: Organisation, tender: Bid, sign_in: SignIn
    ) -> None:
        _, principal = estimator(session, organisation, tender)
        pendent = items(session, tender)[PENDENT]

        response = sign_in(principal).delete(f"/bids/{tender.id}/qto/items/{pendent.id}")

        assert response.status_code == 409


@pytest.mark.req("FR-ADM-02")
class TestRuleVersions:
    def test_an_edit_is_a_new_version_and_old_results_keep_theirs(
        self, session: Session, organisation: Organisation, tender: Bid, sign_in: SignIn
    ) -> None:
        senior = member(session, organisation, tender, "sam", Role.SENIOR_ESTIMATOR)
        client = sign_in(senior)
        before = items(session, tender)[DROPS]
        definition: dict[str, Any] = dict(
            qto.rule_rows(session, organisation.id)["drop_length"].definition
        )
        definition["defaults"] = {**definition["defaults"], "branch_elevation_mm": 3400}

        changed = client.post(
            "/measurement-rules/drop_length",
            json={"definition": definition, "note": "our risers run higher"},
        )
        qto.recompute(session, tender.id)
        session.commit()
        history = client.get("/measurement-rules/drop_length/history").json()
        versions = client.get(f"/bids/{tender.id}/qto/items/{before.id}/history").json()

        assert changed.status_code == 200, changed.text
        assert (changed.json()["version"], changed.json()["status"]) == (2, "confirmed")
        assert [(r["version"], r["retired_at"] is None) for r in history] == [
            (1, False),
            (2, True),
        ]
        assert [(v["version"], v["rule"]["rule_version"], v["net_quantity"]) for v in versions] == [
            (1, 1, "12.000"),
            (2, 2, "14.400"),  # 24 x (3,400 - 2,750 - 50)
        ]

    def test_only_a_senior_estimator_changes_a_rule(
        self, session: Session, organisation: Organisation, tender: Bid, sign_in: SignIn
    ) -> None:
        _, principal = estimator(session, organisation, tender)

        response = sign_in(principal).post(
            "/measurement-rules/drop_length", json={"definition": {}}
        )

        assert response.status_code == 403
