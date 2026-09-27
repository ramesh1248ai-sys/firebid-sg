"""A request's work is committed before its response is sent.

Otherwise a client told "201 Created" can ask for what it created before the commit, and be
told it does not exist: exactly what an end-to-end run on PR #18 saw ("Bid not found" on the
page for a bid just registered). This records the order of the two events through the real
application and its real session dependency.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from firebid.api.app import create_app
from firebid.api.deps import get_principal
from firebid.auth.provisioning import Principal
from firebid.db.models.core import Organisation
from firebid.domain.state_machines import Role
from firebid.settings import Settings
from tests.db.test_bid_workspace import create_bid_payload, make_person


@pytest.fixture
def order(as_application_role: None) -> Iterator[list[str]]:
    seen: list[str] = []

    def committed(_session: Session) -> None:
        seen.append("commit")

    event.listen(Session, "after_commit", committed)
    yield seen
    event.remove(Session, "after_commit", committed)


def recording(app: FastAPI, seen: list[str]) -> Any:
    """The app, noting when the response starts."""

    async def wrapped(scope: Any, receive: Any, send: Any) -> None:
        async def noting(message: Any) -> None:
            if message["type"] == "http.response.start":
                seen.append("response")
            await send(message)

        await app(scope, receive, noting)

    return wrapped


@pytest.mark.req("FR-BID-01")
def test_a_new_bid_is_committed_before_the_client_hears_it_was_created(
    session: Session, organisation: Organisation, order: list[str]
) -> None:
    from firebid.api.deps import SESSION
    from firebid.db.identity import set_transaction_identity

    person = make_person(session, organisation, "bella", {str(Role.BID_MANAGER)})
    app = create_app(Settings(env="test"), health_checks={})

    def signed_in(db: Session = SESSION) -> Principal:
        set_transaction_identity(db, person.user_id)
        return person

    app.dependency_overrides[get_principal] = signed_in
    client = TestClient(recording(app, order))

    response = client.post("/bids", json=create_bid_payload())

    assert response.status_code == 201, response.text
    assert "commit" in order and "response" in order
    # The last commit is the one that saves the bid (signing in may commit earlier).
    last_commit = len(order) - 1 - order[::-1].index("commit")
    assert last_commit < order.index("response"), order
