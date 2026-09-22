"""The audit API: every filter, paging and the CSV export (FR-ADM-04)."""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from firebid.api.app import create_app
from firebid.api.deps import get_session
from firebid.db.audit import record_event
from firebid.db.models.core import AppUser, Bid
from firebid.domain.actors import Actor, AuditContext
from firebid.settings import Settings

pytestmark = pytest.mark.req("FR-ADM-04")


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def session_override() -> Iterator[Session]:
        with factory() as session:
            yield session
            session.commit()

    app = create_app(Settings(env="test"), health_checks={})
    app.dependency_overrides[get_session] = session_override
    with TestClient(app) as client:
        yield client


@pytest.fixture
def history(session: Session, bid: Bid, user: AppUser) -> list[str]:
    """Three events with different actors, entities, actions and times."""
    estimator = Actor(label="Esther Tan", roles=frozenset({"estimator"}), id=user.id)
    system = Actor.system()
    context = AuditContext(organisation_id=bid.organisation_id, bid_id=bid.id)
    base = datetime.now(UTC) - timedelta(hours=3)

    plan = [
        (estimator, "bid lifecycle: start qualification", "bid", str(bid.id), base),
        (
            system,
            "sheet revision: register",
            "sheet_revision",
            str(uuid.uuid4()),
            base + timedelta(hours=1),
        ),
        (estimator, "QTO item: verify", "qto_item", str(uuid.uuid4()), base + timedelta(hours=2)),
    ]
    actions = []
    for actor, action, entity_type, entity_id, occurred_at in plan:
        event = record_event(
            session,
            context=context,
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            after={"state": "changed"},
            reason="because",
        )
        event.occurred_at = occurred_at
        actions.append(action)
        session.commit()
    return actions


class TestFilters:
    def test_returns_newest_first(self, client: TestClient, bid: Bid, history: list[str]) -> None:
        body = client.get("/audit", params={"bid_id": str(bid.id)}).json()
        assert [item["action"] for item in body["items"]] == list(reversed(history))

    def test_filters_by_entity_type(self, client: TestClient, bid: Bid, history: list[str]) -> None:
        body = client.get("/audit", params={"entity_type": "qto_item"}).json()
        assert [item["entity_type"] for item in body["items"]] == ["qto_item"]

    def test_filters_by_entity_id(self, client: TestClient, bid: Bid, history: list[str]) -> None:
        listed = client.get("/audit").json()["items"]
        target = listed[0]["entity_id"]
        body = client.get("/audit", params={"entity_id": target}).json()
        assert {item["entity_id"] for item in body["items"]} == {target}

    def test_filters_by_actor(self, client: TestClient, user: AppUser, history: list[str]) -> None:
        body = client.get("/audit", params={"actor_id": str(user.id)}).json()
        assert len(body["items"]) == 2
        assert {item["actor_label"] for item in body["items"]} == {"Esther Tan"}

    def test_filters_by_action_text(self, client: TestClient, history: list[str]) -> None:
        body = client.get("/audit", params={"action": "verify"}).json()
        assert [item["action"] for item in body["items"]] == ["QTO item: verify"]

    def test_filters_by_time_range(self, client: TestClient, history: list[str]) -> None:
        cutoff = (datetime.now(UTC) - timedelta(hours=1, minutes=30)).isoformat()
        body = client.get("/audit", params={"occurred_from": cutoff}).json()
        assert [item["action"] for item in body["items"]] == ["QTO item: verify"]

    def test_filters_by_bid(self, client: TestClient, bid: Bid, history: list[str]) -> None:
        assert len(client.get("/audit", params={"bid_id": str(bid.id)}).json()["items"]) == 3
        other = client.get("/audit", params={"bid_id": str(uuid.uuid4())}).json()
        assert other["items"] == []


class TestPaging:
    def test_pages_through_with_a_cursor(self, client: TestClient, history: list[str]) -> None:
        first = client.get("/audit", params={"limit": 2}).json()
        assert len(first["items"]) == 2
        assert first["next_cursor"]

        second = client.get("/audit", params={"limit": 2, "cursor": first["next_cursor"]}).json()
        assert len(second["items"]) == 1
        assert second["next_cursor"] is None

        seen = [item["id"] for item in first["items"] + second["items"]]
        assert len(set(seen)) == 3

    def test_a_broken_cursor_is_refused(self, client: TestClient) -> None:
        assert client.get("/audit", params={"cursor": "not-a-cursor"}).status_code == 400

    def test_the_page_size_is_capped(self, client: TestClient) -> None:
        assert client.get("/audit", params={"limit": 500}).status_code == 422


class TestCsvExport:
    def test_exports_the_filtered_rows(self, client: TestClient, history: list[str]) -> None:
        response = client.get("/audit/export.csv", params={"entity_type": "qto_item"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "attachment" in response.headers["content-disposition"]

        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert len(rows) == 1
        assert rows[0]["action"] == "QTO item: verify"
        assert rows[0]["entity_type"] == "qto_item"
        assert rows[0]["actor_label"] == "Esther Tan"

    def test_exports_everything_when_unfiltered(
        self, client: TestClient, history: list[str]
    ) -> None:
        rows = list(csv.DictReader(io.StringIO(client.get("/audit/export.csv").text)))
        assert [row["action"] for row in rows] == history  # oldest first, for reading


class TestChainStatus:
    def test_reports_an_intact_chain(
        self, client: TestClient, bid: Bid, history: list[str]
    ) -> None:
        body = client.get(f"/audit/chain/{bid.id}").json()
        assert body["intact"] is True
        assert body["problems"] == []

    def test_reports_a_broken_chain(
        self, client: TestClient, session: Session, bid: Bid, history: list[str]
    ) -> None:
        session.execute(text("ALTER TABLE audit_event DISABLE TRIGGER audit_event_append_only"))
        session.execute(text("UPDATE audit_event SET reason = 'tampered'"))
        session.execute(text("ALTER TABLE audit_event ENABLE TRIGGER audit_event_append_only"))
        session.commit()

        body = client.get(f"/audit/chain/{bid.id}").json()
        assert body["intact"] is False
        assert any("altered" in problem for problem in body["problems"])
