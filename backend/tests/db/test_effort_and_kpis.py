# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""Time on task and a bid's own KPIs (P1-11; requirements §14, NFR-14, NFR-15)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from firebid.db.models.core import Bid, Organisation
from firebid.db.models.workflow import AgentRun
from firebid.domain.state_machines import Role
from firebid.services import effort, kpis
from tests.db.test_sheet_views import SignIn, app, member, sign_in  # noqa: F401


@pytest.mark.req("NFR-14")
def test_heartbeats_in_one_minute_are_one_minute_and_areas_are_kept_apart(
    session: Session, bid: Bid, organisation: Organisation
) -> None:
    person = member(session, organisation, bid, "esther", Role.ESTIMATOR)
    at = datetime(2026, 9, 29, 9, 30, 5, tzinfo=UTC)

    for second in (5, 20, 55):
        effort.record(session, bid.id, person.user_id, "review", at.replace(second=second))
    effort.record(session, bid.id, person.user_id, "review", at.replace(minute=31))
    effort.record(session, bid.id, person.user_id, "manual", at.replace(minute=31))
    session.commit()

    found = effort.time_on_task(session, bid.id)

    assert (found.minutes, found.by_area, found.people) == (2, {"review": 2, "manual": 1}, 1)


@pytest.mark.req("NFR-14")
def test_the_workbench_heartbeat_through_the_api(
    session: Session, bid: Bid, organisation: Organisation, sign_in: SignIn
) -> None:
    client = sign_in(member(session, organisation, bid, "esther", Role.ESTIMATOR))

    beat = client.post(f"/bids/{bid.id}/review/activity", json={"area": "review"})
    bad = client.post(f"/bids/{bid.id}/review/activity", json={"area": "coffee"})
    worked = client.get(f"/bids/{bid.id}/review/effort").json()

    assert (beat.status_code, bad.status_code) == (204, 422)
    assert worked["minutes"] == 1


@pytest.mark.req("NFR-15")
def test_agent_runs_give_escalation_success_and_cost_by_model(session: Session, bid: Bid) -> None:
    for state, cost in (
        ("succeeded", "0.40"),
        ("succeeded", "0.35"),
        ("escalated", "0.10"),
        ("failed", "0"),
    ):
        session.add(
            AgentRun(
                bid_id=bid.id,
                route="boq_mapping",
                provider="primary",
                model="first",
                state=state,
                cost_sgd=Decimal(cost),
            )
        )
    session.commit()

    found = kpis.bid_kpis(session, bid)

    assert found.agents.runs == 4
    assert found.agents.escalation_rate == pytest.approx(0.25)
    assert found.agents.success_rate == pytest.approx(0.75), "an escalation is not a failure"
    assert found.agents.cost_sgd == Decimal("0.85")
    assert found.agents.by_model[0]["route"] == "boq_mapping"
    assert found.target_cost_sgd is not None


@pytest.mark.req("NFR-15")
def test_the_dashboard_shows_a_member_their_bids_with_cost_by_model(
    session: Session, bid: Bid, organisation: Organisation, sign_in: SignIn
) -> None:
    session.add(
        AgentRun(
            bid_id=bid.id,
            route="rate_match",
            provider="primary",
            model="first",
            state="succeeded",
            cost_sgd=Decimal("0.20"),
        )
    )
    session.commit()
    client = sign_in(member(session, organisation, bid, "esther", Role.ESTIMATOR))

    portfolio = client.get("/kpis").json()
    one = client.get(f"/bids/{bid.id}/kpis").json()

    assert [row["human_id"] for row in portfolio["bids"]] == [bid.human_id]
    assert one["cost_by_model"][0]["route"] == "rate_match"
    assert portfolio["target_cost_sgd"] is not None
