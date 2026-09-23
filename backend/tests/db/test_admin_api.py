"""The admin view: routing, provider health and spend (FR-ADM-05)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from firebid.api.app import create_app
from firebid.api.deps import get_principal, get_session
from firebid.auth.provisioning import Principal
from firebid.db.models.core import AppUser, Bid, BidMember, Organisation
from firebid.db.models.workflow import AgentRun
from firebid.domain.state_machines import Role
from firebid.settings import Settings

SignIn = Callable[[Principal], TestClient]


def person(session: Session, organisation: Organisation, name: str, roles: set[str]) -> Principal:
    user = AppUser(
        organisation_id=organisation.id,
        external_id=uuid.uuid4().hex,
        username=f"{name}@firebid.test",
        display_name=name.title(),
    )
    session.add(user)
    session.commit()
    return Principal(
        user_id=user.id,
        organisation_id=organisation.id,
        username=user.username,
        display_name=user.display_name,
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
    def _sign_in(principal: Principal) -> TestClient:
        app.dependency_overrides[get_principal] = lambda: principal
        return TestClient(app)

    return _sign_in


@pytest.fixture
def administrator(session: Session, organisation: Organisation) -> Principal:
    return person(session, organisation, "admin", {str(Role.SYSTEM_ADMIN)})


@pytest.mark.req("FR-ADM-05")
class TestWhoMaySee:
    def test_an_estimator_is_refused(
        self, session: Session, organisation: Organisation, sign_in: SignIn
    ) -> None:
        estimator = person(session, organisation, "esther", {str(Role.ESTIMATOR)})
        assert sign_in(estimator).get("/admin/llm/routing").status_code == 403

    def test_an_administrator_may(self, sign_in: SignIn, administrator: Principal) -> None:
        assert sign_in(administrator).get("/admin/llm/routing").status_code == 200

    def test_the_commercial_director_may_because_they_own_the_budget(
        self, session: Session, organisation: Organisation, sign_in: SignIn
    ) -> None:
        director = person(session, organisation, "clara", {str(Role.COMMERCIAL_DIRECTOR)})
        assert sign_in(director).get("/admin/llm/cost").status_code == 200


@pytest.mark.req("FR-ADM-05")
class TestRoutingView:
    def test_it_shows_each_route_and_what_would_serve_it(
        self, sign_in: SignIn, administrator: Principal
    ) -> None:
        body = sign_in(administrator).get("/admin/llm/routing").json()
        routes = {route["name"]: route for route in body["routes"]}
        assert "title_block_read" in routes
        assert routes["title_block_read"]["data_class"] == "confidential"
        # The first model whose provider is enabled is what a call would actually use.
        assert routes["title_block_read"]["effective_model"] == "claude-opus-5"
        assert body["config_version"]

    def test_it_shows_each_provider_with_its_approved_data_classes(
        self, sign_in: SignIn, administrator: Principal
    ) -> None:
        """The D2 decision, visible to the people who have to live with it."""
        body = sign_in(administrator).get("/admin/llm/routing").json()
        providers = {provider["name"]: provider for provider in body["providers"]}
        assert "confidential" in providers["anthropic"]["approved_data_classes"]
        assert providers["anthropic"]["no_training"] is True

    def test_it_never_returns_a_credential(self, sign_in: SignIn, administrator: Principal) -> None:
        """Whether a key is configured is useful; the key itself is not."""
        response = sign_in(administrator).get("/admin/llm/routing")
        body = response.text
        assert "credentials_configured" in body
        assert "sk-" not in body
        assert "ANTHROPIC_API_KEY" not in body


@pytest.mark.req("FR-ADM-05")
class TestCostView:
    def _spend(
        self, session: Session, bid: Bid, route: str, provider: str, model: str, cost: str
    ) -> None:
        session.add(
            AgentRun(
                bid_id=bid.id,
                route=route,
                provider=provider,
                model=model,
                state="succeeded",
                tokens_in=1000,
                tokens_out=200,
                cost_sgd=Decimal(cost),
            )
        )
        session.commit()

    def test_spend_groups_by_route_provider_and_model(
        self, session: Session, bid: Bid, sign_in: SignIn, administrator: Principal
    ) -> None:
        session.add(BidMember(bid_id=bid.id, user_id=administrator.user_id, role="system_admin"))
        session.commit()
        self._spend(session, bid, "title_block_read", "anthropic", "claude-opus-5", "3.00")
        self._spend(session, bid, "title_block_read", "openai", "gpt-5.1", "1.00")

        client = sign_in(administrator)

        by_route = client.get("/admin/llm/cost", params={"by": "route"}).json()
        assert by_route[0]["group"] == "title_block_read"
        assert Decimal(by_route[0]["cost_sgd"]) == Decimal("4.00")
        assert by_route[0]["calls"] == 2

        by_provider = {
            row["group"]: row
            for row in client.get("/admin/llm/cost", params={"by": "provider"}).json()
        }
        assert Decimal(by_provider["anthropic"]["cost_sgd"]) == Decimal("3.00")
        assert Decimal(by_provider["openai"]["cost_sgd"]) == Decimal("1.00")

        by_model = {
            row["group"] for row in client.get("/admin/llm/cost", params={"by": "model"}).json()
        }
        assert by_model == {"claude-opus-5", "gpt-5.1"}

    def test_a_bid_reads_as_its_human_identifier(
        self, session: Session, bid: Bid, sign_in: SignIn, administrator: Principal
    ) -> None:
        """A UUID tells a commercial director nothing."""
        session.add(BidMember(bid_id=bid.id, user_id=administrator.user_id, role="system_admin"))
        session.commit()
        self._spend(session, bid, "work", "anthropic", "claude-opus-5", "2.00")

        rows = sign_in(administrator).get("/admin/llm/cost", params={"by": "bid"}).json()
        assert rows[0]["group"] == bid.human_id

    def test_spend_on_a_bid_you_are_not_on_is_not_shown(
        self, session: Session, bid: Bid, sign_in: SignIn, administrator: Principal
    ) -> None:
        """The admin page obeys bid membership like everything else, even for an admin."""
        self._spend(session, bid, "work", "anthropic", "claude-opus-5", "9.00")
        rows = sign_in(administrator).get("/admin/llm/cost", params={"by": "route"}).json()
        assert rows == []

    def test_an_unknown_grouping_is_refused(
        self, sign_in: SignIn, administrator: Principal
    ) -> None:
        assert (
            sign_in(administrator)
            .get("/admin/llm/cost", params={"by": "something_else"})
            .status_code
            == 422
        )


@pytest.mark.req("NFR-05")
def test_breaker_health_is_reported(sign_in: SignIn, administrator: Principal) -> None:
    response = sign_in(administrator).get("/admin/llm/health")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
