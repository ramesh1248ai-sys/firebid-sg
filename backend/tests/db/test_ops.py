"""Operations checks (P1-11): the deployment guard, the provider game day and the restore drill's
pass rules. The drill itself runs against docker (`firebid-ops restore-drill --local`); its
result is recorded evidence, see the build log."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from firebid.ai_gateway.config import load_config
from firebid.db.models.core import Bid
from firebid.ops import game_day
from firebid.ops.deploy_guard import check
from firebid.ops.restore_drill import Drill

WINDOW = datetime(2026, 10, 3, 18, 0, tzinfo=UTC)


@pytest.mark.req("NFR-03")
class TestDeploymentGuard:
    def test_a_window_within_48_hours_of_a_deadline_is_refused(
        self, session: Session, bid: Bid
    ) -> None:
        bid.submission_deadline = WINDOW + timedelta(hours=30)
        session.commit()

        verdict = check(session, WINDOW, WINDOW + timedelta(hours=4))

        assert not verdict.allowed
        [conflict] = [c for c in verdict.conflicts if c.human_id == bid.human_id]
        assert conflict.hours_from_window == pytest.approx(26.0)
        assert "refused" in verdict.summary()

    def test_a_deadline_just_before_the_window_counts_too(self, session: Session, bid: Bid) -> None:
        bid.submission_deadline = WINDOW - timedelta(hours=47)
        session.commit()

        assert not check(session, WINDOW, WINDOW + timedelta(hours=4)).allowed

    def test_a_window_clear_of_every_deadline_is_allowed(self, session: Session, bid: Bid) -> None:
        bid.submission_deadline = WINDOW + timedelta(hours=4 + 49)
        session.commit()

        verdict = check(session, WINDOW, WINDOW + timedelta(hours=4))

        assert bid.human_id not in {c.human_id for c in verdict.conflicts}

    def test_a_submitted_bid_no_longer_holds_maintenance_back(
        self, session: Session, bid: Bid
    ) -> None:
        bid.submission_deadline = WINDOW + timedelta(hours=10)
        bid.state = "submitted"
        session.commit()

        verdict = check(session, WINDOW, WINDOW + timedelta(hours=4))

        assert bid.human_id not in {c.human_id for c in verdict.conflicts}


@pytest.mark.req("NFR-03")
def test_with_the_primary_provider_down_every_route_falls_back_or_escalates_cleanly() -> None:
    config = load_config()

    day = game_day.run(config, game_day.rehearsal_adapters(config, "anthropic"), "anthropic")

    assert day.passed
    assert all(r.served_by != "anthropic" for r in day.routes)
    served = {r.route: r.outcome for r in day.routes}
    assert served["boq_mapping"] == "fallback"
    assert "escalated" in served.values(), "a route with no approved fallback hands over"


@pytest.mark.req("NFR-04")
def test_the_drill_passes_only_inside_rpo_and_rto_with_everything_matching() -> None:
    good = Drill(
        "t",
        "now",
        restore_seconds=600,
        verify_seconds=60,
        backup_age_seconds=3600,
        tables_checked=58,
        chains_checked=83,
    )
    slow = Drill("t", "now", restore_seconds=9 * 3600)
    stale = Drill("t", "now", backup_age_seconds=25 * 3600)
    broken = Drill("t", "now", broken_chains=["x"])

    assert good.passed and "11.0 min" in good.to_json()["summary"]
    assert not slow.passed and not stale.passed and not broken.passed
