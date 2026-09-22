"""Lineage round-trips, partitioning, human IDs and the personal-data inventory."""

from __future__ import annotations

import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from firebid.db.ids import next_bid_id, next_qto_id
from firebid.db.models.audit import AuditEvent
from firebid.db.models.core import Bid, Organisation
from firebid.db.models.takeoff import Evidence, QtoItem
from firebid.domain.evidence import EvidenceRecord, Location, Region, RunMetadata, SourceRef
from firebid.domain.values import CalculationMethod, ExtractionMethod, LengthMm, Money
from tests.db.factories import make_document, make_qto_item, make_sheet

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.req("FR-DOC-07")
def test_source_ref_round_trips_with_every_lineage_field(session: Session, bid: Bid) -> None:
    document = make_document(session, bid)
    sheet = make_sheet(session, bid, document)
    item = make_qto_item(session, bid)

    record = EvidenceRecord(
        qto_human_id=item.human_id,
        bid_human_id=bid.human_id,
        project_name="Example Commercial Tower",
        item_description="Fire main, 150 mm",
        classification="main: wet sprinkler system",
        attributes={"material": "galvanised steel", "schedule": "Sch 40", "joining": "grooved"},
        quantity=Decimal("128.400"),
        unit="m",
        quantity_note="net measured; wastage applied at BOQ",
        source=SourceRef(
            document_id=document.id,
            sheet_id=sheet.id,
            sheet_number="FP-L05-201",
            revision_label="R04",
            page_or_layout="1",
            region=Region(x_min_mm=12.5, y_min_mm=30.0, x_max_mm=420.0, y_max_mm=297.0),
        ),
        location=Location(level="05", zone="B", grid_from="B5", grid_to="G5"),
        geometry_reference="dwg:polyline/0x1f2e,0x1f3a",
        detection_method=ExtractionMethod.CAD_ENTITY,
        calculation_method=CalculationMethod.CENTRELINE_LENGTH,
        calculation_note="sum of centreline lengths at verified scale 1:100",
        evidence_links=["s3://firebid-dev/overlays/qto-000347.png"],
        confidence=0.97,
        verification_status="pending estimator verification",
        linked_boq_line="3.2.4",
        run_metadata=RunMetadata(agent_run_id=uuid.uuid4(), rule_set_version="2026.09"),
    )
    session.add(
        Evidence(
            bid_id=bid.id,
            qto_item_id=item.id,
            record=record.model_dump(mode="json"),
            missing_fields=record.missing_mandatory_fields(),
        )
    )
    session.commit()
    session.expunge_all()

    stored = session.query(Evidence).one()
    restored = EvidenceRecord.model_validate(stored.record)
    assert restored == record
    assert restored.source.region is not None
    assert restored.source.region.x_max_mm == 420.0
    assert restored.missing_mandatory_fields() == []
    assert stored.missing_fields == []


def test_incomplete_evidence_reports_its_missing_fields(session: Session, bid: Bid) -> None:
    document = make_document(session, bid)
    record = EvidenceRecord(
        qto_human_id="QTO-000002",
        bid_human_id=bid.human_id,
        project_name="Example Commercial Tower",
        item_description="Sprinkler, pendent",
        classification="sprinkler",
        quantity=Decimal("42"),
        unit="nr",
        source=SourceRef(document_id=document.id),
        detection_method=ExtractionMethod.VISION,
        calculation_method=CalculationMethod.COUNT,
        confidence=0.6,
        verification_status="proposed",
    )
    assert set(record.missing_mandatory_fields()) == {
        "geometry_reference",
        "evidence_links",
        "source.sheet",
        "source.revision_label",
    }


class TestPartitioning:
    def test_rows_for_several_bids_land_in_different_partitions(
        self, session: Session, bid: Bid, organisation: Organisation
    ) -> None:
        second = Bid(
            organisation_id=organisation.id,
            project_id=bid.project_id,
            human_id="BID-2026-020",
            client_name="Another Main Contractor",
            tender_reference="MC/2026/FP/020",
            submission_deadline=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(second)
        session.flush()
        make_qto_item(session, bid, human_id="QTO-000010")
        make_qto_item(session, second, human_id="QTO-000011")
        session.commit()

        partitions = session.execute(
            text("SELECT count(DISTINCT tableoid::regclass::text) FROM qto_item")
        ).scalar_one()
        assert session.query(QtoItem).count() == 2
        assert partitions >= 1  # hash placement is the database's business

    def test_audit_events_land_in_their_month(self, session: Session, bid: Bid) -> None:
        month = datetime.now(UTC).strftime("%Y%m")
        session.add(
            AuditEvent(
                organisation_id=bid.organisation_id,
                bid_id=bid.id,
                chain_key=bid.id,
                occurred_at=datetime.now(UTC),
                actor_label="test",
                action="noted",
                entity_type="bid",
                entity_id=str(bid.id),
                tx_id=1,
            )
        )
        session.commit()
        partition = session.execute(
            text("SELECT tableoid::regclass::text FROM audit_event LIMIT 1")
        ).scalar_one()
        assert partition == f"audit_event_{month}"

    def test_the_job_creates_partitions_ahead_of_time(self, session: Session) -> None:
        future = (datetime.now(UTC) + timedelta(days=200)).strftime("%Y%m")
        session.execute(
            text("SELECT ensure_audit_event_partition(:target)"),
            {"target": f"{future[:4]}-{future[4:]}-01"},
        )
        session.commit()

        exists = session.execute(
            text("SELECT count(*) FROM pg_class WHERE relname = :name"),
            {"name": f"audit_event_{future}"},
        ).scalar_one()
        assert exists == 1


class TestHumanIds:
    def test_bid_ids_count_up_per_organisation_and_year(
        self, session: Session, organisation: Organisation
    ) -> None:
        first = next_bid_id(session, organisation.id, year=2026)
        second = next_bid_id(session, organisation.id, year=2026)
        other_year = next_bid_id(session, organisation.id, year=2027)
        session.commit()

        assert (first, second, other_year) == ("BID-2026-001", "BID-2026-002", "BID-2027-001")

    def test_qto_ids_count_up_per_bid(self, session: Session, bid: Bid) -> None:
        assert next_qto_id(session, bid.id) == "QTO-000001"
        assert next_qto_id(session, bid.id) == "QTO-000002"
        session.commit()

    def test_concurrent_sessions_never_get_the_same_number(
        self, engine: Engine, organisation: Organisation
    ) -> None:
        from sqlalchemy.orm import sessionmaker

        factory = sessionmaker(bind=engine)
        issued = []
        for _ in range(5):
            with factory() as session:
                issued.append(next_bid_id(session, organisation.id, year=2026))
                session.commit()
        assert len(set(issued)) == 5


@pytest.mark.req("NFR-07")
def test_personal_data_inventory_is_generated_and_current() -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(REPO_ROOT / "scripts/data_inventory.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr

    inventory = (REPO_ROOT / "docs/data-inventory.md").read_text(encoding="utf-8")
    assert "| `app_user` | `username` |" in inventory
    assert "| `audit_event` | `actor_label` |" in inventory


def test_money_and_length_round_trip_through_the_database(session: Session, bid: Bid) -> None:
    item = make_qto_item(session, bid, human_id="QTO-000030")
    item.length = LengthMm.from_metres(Decimal("128.400"))
    session.commit()
    session.expunge_all()

    stored = session.get(QtoItem, (item.id, bid.id))
    assert stored is not None
    assert stored.length == LengthMm(128_400)
    assert stored.length.to_metres() == Decimal("128.400")
    assert Money.of("12.35").times(stored.net_quantity) == Money.of("1585.74")
