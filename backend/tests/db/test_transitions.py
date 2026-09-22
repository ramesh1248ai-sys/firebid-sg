"""State transitions against the database: rules enforced, one audit event each."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from firebid.db.models.audit import AuditEvent
from firebid.db.models.core import AppUser, Bid
from firebid.db.models.takeoff import QtoItem
from firebid.db.models.workflow import Approval
from firebid.domain.actors import Actor
from firebid.domain.state_machines import (
    BidState,
    QtoItemState,
    SheetRevisionState,
    TransitionError,
)
from firebid.services.transitions import apply_transition
from tests.db.factories import make_qto_item, make_sheet_revision


def events_for(session: Session, entity_id: uuid.UUID) -> list[AuditEvent]:
    return list(
        session.execute(select(AuditEvent).where(AuditEvent.entity_id == str(entity_id)))
        .scalars()
        .all()
    )


class TestAuditEventPerTransition:
    def test_one_event_with_before_and_after(
        self, session: Session, bid: Bid, senior_estimator: Actor
    ) -> None:
        apply_transition(
            session,
            bid,
            target=BidState.QUALIFYING,
            actor=senior_estimator,
            reason="tender received",
        )
        session.commit()

        recorded = events_for(session, bid.id)
        assert len(recorded) == 1
        assert recorded[0].before == {"state": "registered"}
        assert recorded[0].after == {"state": "qualifying"}
        assert recorded[0].reason == "tender received"
        assert recorded[0].actor_label == senior_estimator.label
        assert recorded[0].action.startswith("bid lifecycle:")

    def test_a_refused_transition_writes_nothing(
        self, session: Session, bid: Bid, estimator: Actor
    ) -> None:
        with pytest.raises(TransitionError, match="needs one of these roles"):
            apply_transition(session, bid, target=BidState.QUALIFYING, actor=estimator)
        session.commit()

        assert events_for(session, bid.id) == []
        reloaded = session.get(Bid, bid.id)
        assert reloaded is not None
        assert reloaded.state == str(BidState.REGISTERED)

    def test_illegal_target_raises_and_keeps_the_state(
        self, session: Session, bid: Bid, commercial_director: Actor
    ) -> None:
        with pytest.raises(TransitionError, match="cannot move from"):
            apply_transition(session, bid, target=BidState.SUBMITTED, actor=commercial_director)
        assert bid.state == str(BidState.REGISTERED)


class TestGuardsReadTheDatabase:
    def test_submission_needs_a_recorded_g4_approval(
        self, session: Session, bid: Bid, user: AppUser, commercial_director: Actor
    ) -> None:
        bid.state = str(BidState.APPROVED_FOR_SUBMISSION)
        session.flush()

        with pytest.raises(TransitionError, match="G4 submission approval is missing"):
            apply_transition(session, bid, target=BidState.SUBMITTED, actor=commercial_director)

        session.add(
            Approval(
                bid_id=bid.id,
                gate="G4",
                decision="approved",
                approver_id=user.id,
                approver_role="commercial_director",
                decided_at=datetime.now(UTC),
            )
        )
        session.flush()
        apply_transition(session, bid, target=BidState.SUBMITTED, actor=commercial_director)
        assert bid.state == str(BidState.SUBMITTED)

    def test_a_second_current_revision_is_refused(
        self, session: Session, bid: Bid, estimator: Actor
    ) -> None:
        make_sheet_revision(session, bid, state=SheetRevisionState.CURRENT)
        newer = make_sheet_revision(
            session, bid, "FP-L05-201", "R05", SheetRevisionState.REGISTERED
        )

        with pytest.raises(TransitionError, match="already Current"):
            apply_transition(session, newer, target=SheetRevisionState.CURRENT, actor=estimator)

    def test_superseding_the_old_revision_frees_the_new_one(
        self, session: Session, bid: Bid, estimator: Actor
    ) -> None:
        older = make_sheet_revision(session, bid, state=SheetRevisionState.CURRENT)
        newer = make_sheet_revision(
            session, bid, "FP-L05-201", "R05", SheetRevisionState.REGISTERED
        )

        apply_transition(session, older, target=SheetRevisionState.SUPERSEDED, actor=estimator)
        session.flush()
        apply_transition(session, newer, target=SheetRevisionState.CURRENT, actor=estimator)
        session.commit()

        assert newer.state == str(SheetRevisionState.CURRENT)
        assert len(events_for(session, newer.id)) == 1

    def test_an_item_in_a_duplicate_group_cannot_be_baselined(
        self, session: Session, bid: Bid, senior_estimator: Actor
    ) -> None:
        item = make_qto_item(session, bid, QtoItemState.VERIFIED)
        item.duplicate_group_id = uuid.uuid4()
        session.flush()

        with pytest.raises(TransitionError, match="duplicate group"):
            apply_transition(session, item, target=QtoItemState.BASELINED, actor=senior_estimator)

        item.duplicate_group_id = None
        session.flush()
        apply_transition(session, item, target=QtoItemState.BASELINED, actor=senior_estimator)
        assert item.state == str(QtoItemState.BASELINED)


class TestBulkTransitions:
    def test_five_thousand_items_write_five_thousand_events(
        self, session: Session, bid: Bid, estimator: Actor
    ) -> None:
        items = [
            make_qto_item(session, bid, QtoItemState.PROPOSED, f"QTO-{index:06d}")
            for index in range(1, 5001)
        ]
        for item in items:
            apply_transition(session, item, target=QtoItemState.VERIFIED, actor=estimator)
        session.commit()

        verified = session.execute(
            select(func.count())
            .select_from(QtoItem)
            .where(QtoItem.state == str(QtoItemState.VERIFIED))
        ).scalar_one()
        events = session.execute(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.bid_id == bid.id)
        ).scalar_one()
        assert (verified, events) == (5000, 5000)
