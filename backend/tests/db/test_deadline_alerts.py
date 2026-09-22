"""Deadline alerts: who is warned, when, and how often (FR-BID-03).

Time is passed in rather than read from the clock, so every case here is exact.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from firebid.db.models.core import AppUser, Bid, BidMember, Organisation
from firebid.db.models.workflow import DeadlineAlert, HumanTask
from firebid.domain.state_machines import BidState, Role
from firebid.notifications import RecordingNotifier
from firebid.services.alerts import due_alerts, send_deadline_alerts

DEADLINE = datetime(2026, 7, 1, 17, 0, tzinfo=UTC)


def days_before(days: float) -> datetime:
    return DEADLINE - timedelta(days=days)


@pytest.fixture
def alert_bid(session: Session, organisation: Organisation, bid: Bid) -> Bid:
    bid.submission_deadline = DEADLINE
    bid.clarification_cutoff = DEADLINE - timedelta(days=10)
    session.commit()
    return bid


def add_person(session: Session, organisation: Organisation, username: str) -> AppUser:
    person = AppUser(
        organisation_id=organisation.id,
        external_id=uuid.uuid4().hex,
        username=username,
        display_name=username.split("@")[0].title(),
    )
    session.add(person)
    session.flush()
    return person


def assign_task(session: Session, bid: Bid, person: AppUser, state: str = "open") -> HumanTask:
    task = HumanTask(
        bid_id=bid.id,
        kind="review.qto",
        title="Check the rising main take-off",
        state=state,
        assignee_id=person.id,
    )
    session.add(task)
    session.flush()
    return task


def add_member(session: Session, bid: Bid, person: AppUser, role: Role) -> None:
    session.add(BidMember(bid_id=bid.id, user_id=person.id, role=str(role)))
    session.flush()


@pytest.mark.req("FR-BID-03")
class TestWhenAlertsFire:
    def test_nothing_fires_while_the_deadline_is_far_off(
        self, session: Session, alert_bid: Bid
    ) -> None:
        assert due_alerts(session, days_before(8)) == []

    @pytest.mark.parametrize("interval", [7, 3, 1])
    def test_each_configured_interval_fires_once_reached(
        self, session: Session, alert_bid: Bid, interval: int
    ) -> None:
        submission = [
            alert
            for alert in due_alerts(session, days_before(interval))
            if alert.deadline_kind == "submission"
        ]
        assert [alert.days_before for alert in submission] == [interval]

    def test_an_interval_fires_only_once_however_often_the_job_runs(
        self, session: Session, alert_bid: Bid
    ) -> None:
        notifier = RecordingNotifier()
        first = send_deadline_alerts(session, notifier, days_before(6.5))
        session.commit()
        assert [alert.days_before for alert in first if alert.deadline_kind == "submission"] == [7]

        again = send_deadline_alerts(session, notifier, days_before(6))
        session.commit()
        assert [alert.days_before for alert in again if alert.deadline_kind == "submission"] == []

    def test_a_tighter_interval_still_fires_after_the_wider_one(
        self, session: Session, alert_bid: Bid
    ) -> None:
        send_deadline_alerts(session, RecordingNotifier(), days_before(7))
        session.commit()
        later = send_deadline_alerts(session, RecordingNotifier(), days_before(2.5))
        session.commit()
        kinds = [(alert.deadline_kind, alert.days_before) for alert in later]
        assert ("submission", 3) in kinds

    def test_a_passed_deadline_stops_alerting(self, session: Session, alert_bid: Bid) -> None:
        after = DEADLINE + timedelta(hours=1)
        assert [
            alert for alert in due_alerts(session, after) if alert.deadline_kind == "submission"
        ] == []

    def test_a_submitted_bid_stops_alerting(self, session: Session, alert_bid: Bid) -> None:
        alert_bid.state = str(BidState.SUBMITTED)
        session.commit()
        assert due_alerts(session, days_before(1)) == []

    def test_the_clarification_cutoff_alerts_on_its_own_schedule(
        self, session: Session, alert_bid: Bid
    ) -> None:
        # Ten days before submission, so at 11 days out the cut-off is one day away.
        alerts = due_alerts(session, DEADLINE - timedelta(days=11))
        assert [(a.deadline_kind, a.days_before) for a in alerts] == [("clarification_cutoff", 1)]

    def test_intervals_are_per_organisation(
        self, session: Session, organisation: Organisation, alert_bid: Bid
    ) -> None:
        organisation.deadline_alert_days = [14, 2]
        session.commit()
        # Outside this organisation's widest interval, nothing fires; the default 7 does not apply.
        assert due_alerts(session, days_before(30)) == []
        submission = [
            a for a in due_alerts(session, days_before(13)) if a.deadline_kind == "submission"
        ]
        assert [a.days_before for a in submission] == [14]

    def test_a_window_already_entered_still_warns_on_the_first_run(
        self, session: Session, organisation: Organisation, alert_bid: Bid
    ) -> None:
        """A job that starts late must not swallow the warning it never sent."""
        organisation.deadline_alert_days = [14, 2]
        session.commit()
        submission = [
            a for a in due_alerts(session, days_before(7)) if a.deadline_kind == "submission"
        ]
        assert [a.days_before for a in submission] == [14]


@pytest.mark.req("FR-BID-03")
class TestWhoIsTold:
    def test_the_people_with_open_work_on_the_bid_are_told(
        self, session: Session, organisation: Organisation, alert_bid: Bid, user: AppUser
    ) -> None:
        busy = add_person(session, organisation, "busy@firebid.test")
        finished = add_person(session, organisation, "finished@firebid.test")
        assign_task(session, alert_bid, busy)
        assign_task(session, alert_bid, finished, state="done")
        session.commit()

        notifier = RecordingNotifier()
        send_deadline_alerts(session, notifier, days_before(1))
        session.commit()

        assert {n.recipient_email for n in notifier.sent} == {"busy@firebid.test"}

    def test_with_no_open_work_the_bid_managers_are_told(
        self, session: Session, organisation: Organisation, alert_bid: Bid, user: AppUser
    ) -> None:
        """Only the managers, not every member: an estimator with nothing open is not chased."""
        estimator = add_person(session, organisation, "estimator2@firebid.test")
        add_member(session, alert_bid, estimator, Role.ESTIMATOR)
        session.commit()

        notifier = RecordingNotifier()
        send_deadline_alerts(session, notifier, days_before(1))
        session.commit()

        assert {n.recipient_email for n in notifier.sent} == {user.username}

    def test_the_message_names_the_bid_and_the_deadline(
        self, session: Session, organisation: Organisation, alert_bid: Bid, user: AppUser
    ) -> None:
        session.commit()

        notifier = RecordingNotifier()
        send_deadline_alerts(session, notifier, days_before(1))
        session.commit()

        message = next(n for n in notifier.sent if n.kind == "deadline.submission")
        assert alert_bid.human_id in message.subject
        assert "1 day" in message.subject
        assert alert_bid.client_name in message.body
        assert "01 Jul 2026" in message.body

    def test_people_on_another_bid_are_not_told(
        self, session: Session, organisation: Organisation, alert_bid: Bid, user: AppUser
    ) -> None:
        other_bid = Bid(
            organisation_id=organisation.id,
            project_id=alert_bid.project_id,
            human_id="BID-2026-099",
            client_name="Unrelated Pte Ltd",
            tender_reference="UR/2026/FP/099",
            submission_deadline=DEADLINE + timedelta(days=60),
        )
        session.add(other_bid)
        session.flush()
        outsider = add_person(session, organisation, "outsider@firebid.test")
        assign_task(session, other_bid, outsider)
        session.commit()

        notifier = RecordingNotifier()
        send_deadline_alerts(session, notifier, days_before(1))
        session.commit()

        assert "outsider@firebid.test" not in {n.recipient_email for n in notifier.sent}


@pytest.mark.req("FR-BID-03")
def test_what_was_sent_is_recorded(session: Session, alert_bid: Bid, user: AppUser) -> None:
    session.commit()

    send_deadline_alerts(session, RecordingNotifier(), days_before(1))
    session.commit()

    records = session.query(DeadlineAlert).filter_by(bid_id=alert_bid.id).all()
    assert {(r.deadline_kind, r.days_before) for r in records} >= {("submission", 1)}
    assert all(r.recipient_count == 1 for r in records if r.deadline_kind == "submission")
