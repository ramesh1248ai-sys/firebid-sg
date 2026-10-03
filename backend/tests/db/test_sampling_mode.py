# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""Sampling mode through the database and the API (FR-REV-05).

Two categories of 40 items each on one bid, both under a sampling policy of 5% tolerable
error at 95% confidence, which takes a sample of 25. One is reviewed without error and is
accepted on its sample; the other has an error seeded in its sample and goes back to full
review.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.auth.provisioning import Principal
from firebid.db.models.audit import AuditEvent
from firebid.db.models.baseline import CoveragePolicy, ReviewSample
from firebid.db.models.core import Bid, Organisation
from firebid.db.models.takeoff import QtoItem
from firebid.domain.state_machines import Role
from firebid.services import qto, review, review_actions, sampling
from tests.db.test_sheet_views import SignIn, app, member, sign_in  # noqa: F401

pytestmark = pytest.mark.req("FR-REV-05")

LOT = 40
SAMPLE = 25  # the plan for 40 items at 5% and 95%: see tests/qto/test_sampling.py
EVIDENCE = "P1 exit report: 100% count accuracy on 12 tenders"


def lot(session: Session, bid: Bid, category: str, start: int) -> list[QtoItem]:
    made = []
    for number in range(start, start + LOT):
        item = QtoItem(
            bid_id=bid.id,
            human_id=f"QTO-{number:06d}",
            item_type=category,
            classification=category,
            description=f"{category} {number}",
            unit="no",
            net_quantity=Decimal(4),
            calculation_method="count",
            confidence=0.95,
            level="L01",
            state="proposed",
        )
        session.add(item)
        made.append(item)
    session.flush()
    return made


@pytest.fixture
def senior(session: Session, organisation: Organisation, bid: Bid) -> Principal:
    return member(session, organisation, bid, "sam", Role.SENIOR_ESTIMATOR)


@pytest.fixture
def estimator(session: Session, organisation: Organisation, bid: Bid) -> Principal:
    return member(session, organisation, bid, "esther", Role.ESTIMATOR)


@pytest.fixture
def sampled(session: Session, organisation: Organisation, bid: Bid, senior: Principal) -> Bid:
    """Sprinklers and valves under sampling; 40 of each on the bid, all proposed."""
    for category in ("sprinkler", "valve"):
        sampling.set_policy(
            session, organisation.id, category, mode="sampling", actor=senior.actor(), note=EVIDENCE
        )
    lot(session, bid, "sprinkler", 1)
    lot(session, bid, "valve", 101)
    session.commit()
    return bid


def by_human_id(session: Session, bid: Bid) -> dict[str, QtoItem]:
    return {item.human_id: item for item in qto.live_items(session, bid.id)}


class TestThePolicy:
    def test_a_category_is_in_full_review_until_a_policy_says_otherwise(
        self, session: Session, organisation: Organisation
    ) -> None:
        assert sampling.policy_for(session, organisation.id, "sprinkler").mode == "full"

    def test_an_edit_is_a_new_version_and_is_audited(
        self, session: Session, organisation: Organisation, senior: Principal
    ) -> None:
        actor = senior.actor()
        sampling.set_policy(
            session, organisation.id, "sprinkler", mode="sampling", actor=actor, note=EVIDENCE
        )
        second = sampling.set_policy(
            session,
            organisation.id,
            "sprinkler",
            mode="sampling",
            actor=actor,
            tolerable_error_percent=2.5,
            note="tightened after the March audit",
        )

        rows = list(
            session.execute(
                select(CoveragePolicy).where(CoveragePolicy.organisation_id == organisation.id)
            ).scalars()
        )
        assert sorted((r.version, r.retired_at is None) for r in rows) == [(1, False), (2, True)]
        assert sampling.policy_for(session, organisation.id, "sprinkler").version == 2
        events = list(
            session.execute(
                select(AuditEvent).where(AuditEvent.action == "coverage policy: new version")
            ).scalars()
        )
        latest = next(e for e in events if str(e.entity_id) == str(second.id))
        assert latest.actor_id == actor.id and latest.reason == "tightened after the March audit"
        assert latest.before["tolerable_error_percent"] == "5"  # type: ignore[index]
        assert latest.after["tolerable_error_percent"] == "2.5"  # type: ignore[index]

    def test_sampling_needs_the_accuracy_evidence_it_rests_on(
        self, session: Session, organisation: Organisation, senior: Principal
    ) -> None:
        with pytest.raises(sampling.SamplingError, match="accuracy evidence"):
            sampling.set_policy(
                session, organisation.id, "sprinkler", mode="sampling", actor=senior.actor()
            )

    def test_only_a_senior_estimator_sets_it(
        self, senior: Principal, estimator: Principal, sign_in: SignIn
    ) -> None:
        body = {"mode": "sampling", "note": EVIDENCE}

        refused = sign_in(estimator).post("/coverage-policies/sprinkler", json=body)
        allowed = sign_in(senior).post("/coverage-policies/sprinkler", json=body)

        assert refused.status_code == 403
        assert allowed.status_code == 200 and allowed.json()["version"] == 1
        listed = sign_in(senior).get("/coverage-policies").json()
        assert [(p["category"], p["mode"]) for p in listed] == [("sprinkler", "sampling")]


class TestWithinTheThreshold:
    def test_the_sample_is_drawn_at_random_and_recorded(
        self, session: Session, sampled: Bid, estimator: Principal
    ) -> None:
        sample = sampling.draw(session, sampled, "sprinkler", estimator.actor(), seed=20261003)

        stored = session.execute(
            select(ReviewSample).where(ReviewSample.id == sample.id)
        ).scalar_one()
        assert len(stored.sample) == SAMPLE and len(stored.lot) == LOT
        assert set(stored.sample) < set(stored.lot)
        assert (stored.seed, stored.status, stored.drawn_by) == ("20261003", "drawn", "Esther")
        assert stored.plan["sample_size"] == SAMPLE and stored.plan["accept_errors"] == 0
        # Anyone can draw it again from what is recorded.
        from firebid.qto.sampling import draw

        assert draw(list(stored.lot), SAMPLE, int(stored.seed)) == list(stored.sample)
        event = session.execute(
            select(AuditEvent).where(AuditEvent.action == "review sample: drawn")
        ).scalar_one()
        assert event.after["sample"] == list(stored.sample)  # type: ignore[index]

    def test_a_clean_sample_passes_and_the_category_is_accepted_on_it(
        self, session: Session, sampled: Bid, estimator: Principal, senior: Principal
    ) -> None:
        sample = sampling.draw(session, sampled, "sprinkler", estimator.actor(), seed=7)
        items = by_human_id(session, sampled)
        with pytest.raises(sampling.SamplingError, match="25 sampled item"):
            sampling.accept_on_sample(session, sampled, "sprinkler", senior.actor())

        review_actions.accept(
            session, sampled.id, [items[h].id for h in sample.sample], estimator.actor()
        )
        assert sampling.refresh(session, sample).status == "passed"
        accepted = sampling.accept_on_sample(session, sampled, "sprinkler", senior.actor())

        after = by_human_id(session, sampled)
        sprinklers = [i for i in after.values() if i.classification == "sprinkler"]
        assert {i.state for i in sprinklers} == {"verified"}
        on_sample = [i for i in sprinklers if dict(i.derivation).get(sampling.ACCEPTED)]
        assert len(on_sample) == LOT - SAMPLE
        assert {i.verified_by_id for i in on_sample} == {senior.user_id}
        assert (accepted.status, accepted.decided_by, accepted.errors) == ("accepted", "Sam", 0)
        [row] = [r for r in sampling.summary(session, sampled) if r["category"] == "sprinkler"]
        assert (row["verified_individually"], row["accepted_on_sample"], row["met"]) == (
            SAMPLE,
            LOT - SAMPLE,
            True,
        )

    def test_an_item_that_joins_the_category_later_is_not_accepted_with_it(
        self, session: Session, sampled: Bid, estimator: Principal, senior: Principal
    ) -> None:
        sample = sampling.draw(session, sampled, "sprinkler", estimator.actor(), seed=7)
        items = by_human_id(session, sampled)
        review_actions.accept(
            session, sampled.id, [items[h].id for h in sample.sample], estimator.actor()
        )
        late = QtoItem(
            bid_id=sampled.id,
            human_id="QTO-000900",
            item_type="sprinkler",
            classification="sprinkler",
            description="added by an addendum",
            unit="no",
            net_quantity=Decimal(2),
            calculation_method="count",
            state="proposed",
        )
        session.add(late)
        session.flush()

        sampling.accept_on_sample(session, sampled, "sprinkler", senior.actor())

        assert by_human_id(session, sampled)["QTO-000900"].state == "proposed"


class TestAboveTheThreshold:
    def escalate(self, session: Session, bid: Bid, estimator: Principal) -> ReviewSample:
        sample = sampling.draw(session, bid, "valve", estimator.actor(), seed=11)
        items = by_human_id(session, bid)
        wrong, *right = sample.sample
        review_actions.edit(
            session,
            bid.id,
            items[wrong].id,
            estimator.actor(),
            "wrong_quantity",
            quantity=Decimal(5),
        )
        review_actions.accept(session, bid.id, [items[h].id for h in right], estimator.actor())
        return sampling.refresh(session, sample)

    def test_an_error_over_the_threshold_sends_the_category_back_to_full_review(
        self, session: Session, sampled: Bid, estimator: Principal, senior: Principal
    ) -> None:
        sample = self.escalate(session, sampled, estimator)

        assert (sample.status, sample.errors) == ("escalated", 1)
        with pytest.raises(sampling.SamplingError, match="full review"):
            sampling.accept_on_sample(session, sampled, "valve", senior.actor())
        with pytest.raises(sampling.SamplingError, match="full review"):
            sampling.draw(session, sampled, "valve", estimator.actor())
        valves = [i for i in by_human_id(session, sampled).values() if i.classification == "valve"]
        assert sum(1 for i in valves if i.state == "proposed") == LOT - SAMPLE
        [row] = [r for r in sampling.summary(session, sampled) if r["category"] == "valve"]
        assert (row["mode"], row["policy_mode"], row["met"]) == ("full", "sampling", False)

    def test_g1_coverage_is_short_until_every_item_of_it_is_verified(
        self, session: Session, sampled: Bid, estimator: Principal, senior: Principal
    ) -> None:
        self.escalate(session, sampled, estimator)
        sprinklers = sampling.draw(session, sampled, "sprinkler", estimator.actor(), seed=3)
        items = by_human_id(session, sampled)
        review_actions.accept(
            session, sampled.id, [items[h].id for h in sprinklers.sample], estimator.actor()
        )
        sampling.accept_on_sample(session, sampled, "sprinkler", senior.actor())

        short = review.coverage(session, sampled.id)
        left = [i.id for i in by_human_id(session, sampled).values() if i.state == "proposed"]
        review_actions.accept(session, sampled.id, left, estimator.actor())
        met = review.coverage(session, sampled.id)

        assert not short["met"] and short["items_verified"] == LOT + SAMPLE
        assert met["met"] and met["items_verified"] == 2 * LOT

    def test_a_category_in_full_review_cannot_be_sampled(
        self, session: Session, sampled: Bid, estimator: Principal
    ) -> None:
        lot(session, sampled, "device", 201)

        with pytest.raises(sampling.SamplingError, match="policy is not sampling"):
            sampling.draw(session, sampled, "device", estimator.actor())


class TestTheApi:
    def test_draw_review_and_accept(
        self,
        session: Session,
        sampled: Bid,
        estimator: Principal,
        senior: Principal,
        sign_in: SignIn,
    ) -> None:
        client = sign_in(estimator)
        drawn = client.post(f"/bids/{sampled.id}/review/sampling/sprinkler/draw")
        assert drawn.status_code == 200, drawn.text
        sample = drawn.json()
        items = by_human_id(session, sampled)
        client.post(
            f"/bids/{sampled.id}/review/accept",
            json={"item_ids": [str(items[h].id) for h in sample["sample"]]},
        )

        refused = client.post(f"/bids/{sampled.id}/review/sampling/sprinkler/accept")
        accepted = sign_in(senior).post(f"/bids/{sampled.id}/review/sampling/sprinkler/accept")
        summary = sign_in(senior).get(f"/bids/{sampled.id}/review/sampling").json()

        assert refused.status_code == 403, "accepting a category is the Senior Estimator's"
        assert accepted.status_code == 200 and accepted.json()["status"] == "accepted"
        row = next(r for r in summary if r["category"] == "sprinkler")
        assert row["accepted_on_sample"] == LOT - SAMPLE and row["sample"]["seed"] == sample["seed"]
        again = sign_in(estimator).post(f"/bids/{sampled.id}/review/sampling/sprinkler/draw")
        assert again.status_code == 409
