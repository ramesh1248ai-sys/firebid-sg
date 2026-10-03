"""The acceptance-sampling plan, the draw and the verdict (FR-REV-05)."""

from __future__ import annotations

from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from firebid.qto import sampling
from firebid.qto.sampling import Policy

pytestmark = pytest.mark.req("FR-REV-05")

POLICY = Policy("sprinkler", "sampling", tolerable_error_percent=5, confidence_percent=95)


class TestThePlan:
    def test_the_chance_of_passing_is_the_hypergeometric_s(self) -> None:
        # 10 items, 2 wrong, 3 drawn, none accepted: C(8,3) / C(10,3) = 56 / 120.
        assert sampling.chance_of_passing(10, 2, 3, 0) == Fraction(56, 120)
        # One accepted: add the samples holding exactly one wrong item, 2 x C(8,2) = 56.
        assert sampling.chance_of_passing(10, 2, 3, 1) == Fraction(112, 120)

    @pytest.mark.parametrize(
        ("lot", "size"),
        # By hand: a lot at 5% holds floor(0.05 N) + 1 wrong items; the sample is the
        # smallest whose chance of missing them all is at most 5%.
        [(1, 1), (10, 10), (20, 16), (40, 25), (100, 39), (500, 54)],
    )
    def test_sample_size_from_lot_size_and_quality_level(self, lot: int, size: int) -> None:
        plan = sampling.plan(lot, POLICY)

        assert plan.sample_size == size
        wrong = lot * 5 // 100 + 1
        assert plan.tolerable_errors == wrong
        assert sampling.chance_of_passing(lot, wrong, size, 0) <= Fraction(5, 100)
        if size > 1 and size < lot:
            assert sampling.chance_of_passing(lot, wrong, size - 1, 0) > Fraction(5, 100)

    def test_a_looser_quality_level_or_lower_confidence_takes_a_smaller_sample(self) -> None:
        strict = sampling.plan(200, POLICY).sample_size
        looser = sampling.plan(200, Policy("x", "sampling", 10, 95)).sample_size
        less_sure = sampling.plan(200, Policy("x", "sampling", 5, 80)).sample_size

        assert looser < strict and less_sure < strict

    def test_accepting_an_error_takes_a_larger_sample(self) -> None:
        none = sampling.plan(200, POLICY).sample_size
        one = sampling.plan(200, Policy("x", "sampling", 5, 95, accept_errors=1)).sample_size

        assert one > none

    def test_an_empty_lot_needs_no_sample(self) -> None:
        assert sampling.plan(0, POLICY).sample_size == 0

    @pytest.mark.parametrize(
        "policy",
        [
            Policy("x", "sometimes"),
            Policy("x", "sampling", tolerable_error_percent=0),
            Policy("x", "sampling", confidence_percent=100),
            Policy("x", "sampling", accept_errors=-1),
        ],
    )
    def test_a_policy_that_makes_no_sense_is_refused(self, policy: Policy) -> None:
        with pytest.raises(ValueError):
            sampling.plan(50, policy)

    @given(
        lot=st.integers(1, 400),
        rate=st.sampled_from([1, 2.5, 5, 10]),
        confidence=st.sampled_from([80, 90, 95, 99]),
        accept=st.integers(0, 2),
    )
    def test_the_plan_always_meets_its_confidence_or_reviews_the_whole_lot(
        self, lot: int, rate: float, confidence: float, accept: int
    ) -> None:
        policy = Policy("x", "sampling", rate, confidence, accept)

        plan = sampling.plan(lot, policy)

        assert 1 <= plan.sample_size <= lot
        if not plan.full:
            chance = sampling.chance_of_passing(
                lot, plan.tolerable_errors, plan.sample_size, accept
            )
            assert chance <= 1 - Fraction(str(confidence)) / 100


class TestTheDraw:
    LOT = tuple(f"QTO-{n:06d}" for n in range(1, 41))

    def test_the_same_lot_and_seed_give_the_same_sample(self) -> None:
        first = sampling.draw(list(self.LOT), 25, seed=20261003)
        second = sampling.draw(list(reversed(self.LOT)), 25, seed=20261003)

        assert first == second and len(set(first)) == 25 and set(first) <= set(self.LOT)

    def test_another_seed_gives_another_sample(self) -> None:
        assert sampling.draw(list(self.LOT), 25, seed=1) != sampling.draw(
            list(self.LOT), 25, seed=2
        )

    def test_a_sample_as_large_as_the_lot_is_the_lot(self) -> None:
        assert sampling.draw(list(self.LOT), 40, seed=1) == sorted(self.LOT)

    def test_every_item_is_as_likely_as_any_other(self) -> None:
        drawn = [item for seed in range(400) for item in sampling.draw(list(self.LOT), 10, seed)]

        counts = [drawn.count(item) for item in self.LOT]
        assert min(counts) > 60 and max(counts) < 140  # 100 each on average


class TestTheVerdict:
    def test_a_sample_within_the_threshold_passes_once_it_is_all_reviewed(self) -> None:
        partly = sampling.evaluate({"a": "correct", "b": None}, accept_errors=0)
        done = sampling.evaluate({"a": "correct", "b": "correct"}, accept_errors=0)

        assert (partly.status, partly.reviewed) == ("drawn", 1)
        assert (done.status, done.errors, done.passed) == ("passed", 0, True)

    def test_errors_above_the_threshold_escalate_at_once(self) -> None:
        verdict = sampling.evaluate({"a": "error", "b": None, "c": None}, accept_errors=0)

        assert verdict.status == "escalated" and not verdict.passed and not verdict.complete

    def test_errors_up_to_the_accepted_number_still_pass(self) -> None:
        verdict = sampling.evaluate({"a": "error", "b": "correct"}, accept_errors=1)

        assert verdict.status == "passed"
