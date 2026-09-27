"""The bid workspace API: creation, the FR-BID-01 rule, the dashboard, and route scoping."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from firebid.api.app import create_app
from firebid.api.deps import get_principal, get_session
from firebid.auth.permissions import GATE_ACTIONS, roles_for
from firebid.auth.provisioning import Principal
from firebid.db.models.core import AppUser, Bid, BidMember, Organisation
from firebid.domain.state_machines import BidState, Role
from firebid.settings import Settings

SignIn = Callable[[Principal], TestClient]


def make_person(
    session: Session, organisation: Organisation, name: str, roles: set[str]
) -> Principal:
    person = AppUser(
        organisation_id=organisation.id,
        external_id=uuid.uuid4().hex,
        username=f"{name}@firebid.test",
        display_name=name.title(),
    )
    session.add(person)
    session.flush()
    from firebid.db.models.core import UserRole

    for role in roles:
        session.add(UserRole(user_id=person.id, role=role))
    session.commit()
    return Principal(
        user_id=person.id,
        organisation_id=organisation.id,
        username=person.username,
        display_name=person.display_name,
        roles=frozenset(roles),
    )


@pytest.fixture
def app(engine: Engine) -> FastAPI:
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def session_override() -> Iterator[Session]:
        with factory() as session:
            yield session
            session.commit()

    application = create_app(Settings(env="test"), health_checks={})
    application.dependency_overrides[get_session] = session_override
    return application


@pytest.fixture
def sign_in(app: FastAPI) -> SignIn:
    """A client signed in as the given person, without going through the identity provider."""

    def _sign_in(principal: Principal) -> TestClient:
        app.dependency_overrides[get_principal] = lambda: principal
        return TestClient(app)

    return _sign_in


@pytest.fixture
def bid_manager(session: Session, organisation: Organisation) -> Principal:
    return make_person(session, organisation, "bella", {str(Role.BID_MANAGER)})


@pytest.fixture
def outsider(session: Session, organisation: Organisation) -> Principal:
    return make_person(session, organisation, "olivia", {str(Role.ESTIMATOR)})


def create_bid_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "project_name": "Example Commercial Tower",
        "client_name": "Main Contractor Pte Ltd",
        "tender_reference": "MC/2026/FP/014",
        "submission_deadline": (datetime.now(UTC) + timedelta(days=14)).isoformat(),
        "clarification_cutoff": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        "tender_validity_days": 90,
    }
    payload.update(overrides)
    return payload


class TestCreateAndComplete:
    @pytest.mark.req("FR-BID-01")
    def test_a_bid_starts_registered_and_lists_nothing_missing(
        self, sign_in: SignIn, bid_manager: Principal
    ) -> None:
        client = sign_in(bid_manager)
        created = client.post("/bids", json=create_bid_payload()).json()

        assert created["state"] == str(BidState.REGISTERED)
        assert created["human_id"].startswith("BID-")
        assert created["missing_mandatory_fields"] == []

    @pytest.mark.req("FR-VIS-02")
    def test_the_consultant_is_kept_on_the_project_and_can_be_changed(
        self, sign_in: SignIn, bid_manager: Principal
    ) -> None:
        """Symbol mappings are remembered per consultant, so a bid must be able to say whose
        drawings it holds."""
        client = sign_in(bid_manager)
        created = client.post(
            "/bids", json=create_bid_payload(consultant="Alpha Consultants Pte Ltd")
        ).json()
        assert created["consultant"] == "Alpha Consultants Pte Ltd"

        changed = client.patch(
            f"/bids/{created['id']}", json={"consultant": "Beta Engineering"}
        ).json()

        assert changed["consultant"] == "Beta Engineering"
        assert client.get(f"/bids/{created['id']}").json()["consultant"] == "Beta Engineering"

    @pytest.mark.req("FR-BID-01")
    def test_work_cannot_start_while_details_are_missing(
        self, sign_in: SignIn, bid_manager: Principal
    ) -> None:
        client = sign_in(bid_manager)
        created = client.post(
            "/bids",
            json=create_bid_payload(clarification_cutoff=None, tender_validity_days=None),
        ).json()
        assert set(created["missing_mandatory_fields"]) == {
            "clarification_cutoff",
            "tender_validity_days",
        }

        refused = client.post(
            f"/bids/{created['id']}/transitions", json={"target": str(BidState.QUALIFYING)}
        )
        assert refused.status_code == 409
        assert "clarification_cutoff" in refused.json()["detail"]

    @pytest.mark.req("FR-BID-01")
    def test_work_starts_once_the_details_are_complete(
        self, sign_in: SignIn, bid_manager: Principal
    ) -> None:
        client = sign_in(bid_manager)
        created = client.post("/bids", json=create_bid_payload(clarification_cutoff=None)).json()

        client.patch(
            f"/bids/{created['id']}",
            json={"clarification_cutoff": (datetime.now(UTC) + timedelta(days=5)).isoformat()},
        )
        moved = client.post(
            f"/bids/{created['id']}/transitions",
            json={"target": str(BidState.QUALIFYING), "reason": "details complete"},
        )
        assert moved.status_code == 200
        assert moved.json()["state"] == str(BidState.QUALIFYING)


class TestDashboard:
    @pytest.mark.req("FR-BID-02")
    def test_shows_stage_deadlines_and_task_counts(
        self, sign_in: SignIn, bid_manager: Principal
    ) -> None:
        client = sign_in(bid_manager)
        created = client.post("/bids", json=create_bid_payload()).json()
        client.post(
            f"/bids/{created['id']}/tasks",
            json={
                "title": "Confirm tender documents are complete",
                "stage": "S1",
                "due_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            },
        )

        row = client.get("/bids").json()[0]
        assert row["stage"] == "S0"
        assert row["open_tasks"] == 1
        assert row["overdue_tasks"] == 1
        assert row["days_to_submission"] in (13, 14)
        assert row["gates_passed"] == []

    def test_lists_only_the_caller_s_bids(
        self, sign_in: SignIn, bid_manager: Principal, outsider: Principal
    ) -> None:
        sign_in(bid_manager).post("/bids", json=create_bid_payload())
        assert sign_in(outsider).get("/bids").json() == []


class TestScoping:
    @pytest.mark.req("NFR-08")
    def test_every_bid_route_hides_the_bid_from_a_non_member(
        self, app: FastAPI, sign_in: SignIn, bid_manager: Principal, outsider: Principal
    ) -> None:
        """Walks the published API, so a new unscoped bid route fails this test."""
        created = sign_in(bid_manager).post("/bids", json=create_bid_payload()).json()
        client = sign_in(outsider)

        checked = 0
        for path, operations in app.openapi()["paths"].items():
            if "{bid_id}" not in path:
                continue
            for method in operations:
                if method.upper() in {"HEAD", "OPTIONS", "TRACE"}:
                    continue
                url = path.replace("{bid_id}", created["id"])
                response = client.request(method.upper(), url, json={})
                assert response.status_code == 404, (
                    f"{method.upper()} {url} returned {response.status_code}; "
                    "bid routes must hide the bid from non-members"
                )
                checked += 1
        assert checked >= 6, "expected several bid-owned routes to check"

    @pytest.mark.req("NFR-08")
    def test_a_member_of_another_bid_still_cannot_reach_this_one(
        self,
        session: Session,
        sign_in: SignIn,
        bid_manager: Principal,
        outsider: Principal,
        bid: Bid,
    ) -> None:
        session.add(BidMember(bid_id=bid.id, user_id=outsider.user_id, role="estimator"))
        session.commit()
        created = sign_in(bid_manager).post("/bids", json=create_bid_payload()).json()

        client = sign_in(outsider)
        assert client.get(f"/bids/{created['id']}").status_code == 404
        assert client.get(f"/bids/{bid.id}").status_code == 200


class TestPermissions:
    @pytest.mark.req("FR-ADM-01")
    def test_each_gate_is_approvable_only_by_its_accountable_role(self) -> None:
        """Requirements §3.2: the Accountable role signs the gate."""
        accountable = {
            "G0": Role.COMMERCIAL_DIRECTOR,
            "G1": Role.SENIOR_ESTIMATOR,
            "G2": Role.SENIOR_ESTIMATOR,
            "G3": Role.COMMERCIAL_DIRECTOR,
            "G4": Role.COMMERCIAL_DIRECTOR,
        }
        for gate, role in accountable.items():
            assert roles_for(GATE_ACTIONS[gate]) == frozenset({role}), gate

    @pytest.mark.req("FR-ADM-01")
    def test_creating_a_bid_needs_a_permitted_role(
        self, sign_in: SignIn, outsider: Principal
    ) -> None:
        response = sign_in(outsider).post("/bids", json=create_bid_payload())
        assert response.status_code == 403
