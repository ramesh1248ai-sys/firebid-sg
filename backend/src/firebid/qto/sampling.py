"""Acceptance sampling for item categories whose accuracy is proven (FR-REV-05).

A category under sampling is not reviewed item by item. A random sample of its items is
reviewed; if the sample shows no more errors than the plan accepts, the category is accepted
on the sample, and if it shows more, the whole category goes back to full review.

**The plan** is worked out from three numbers a Senior Estimator sets for the category:

* the **tolerable error rate** (the acceptable quality level): the share of wrong items the
  company will not accept in a category it does not fully review;
* the **confidence** that a category as bad as that is caught;
* the **errors accepted** in the sample (usually none).

The sample size is the smallest for which a lot with the tolerable share of wrong items
would show more errors than accepted with at least the stated confidence. It is exact for
the lot's size (drawing without replacement, the hypergeometric distribution), in whole
numbers: no approximation is rounded in the platform's favour. A small lot is therefore
reviewed in full, and a lot too small to hold a single tolerable error always is.

**The draw** is random and reproducible: the seed is recorded with the lot and the sample,
so anyone can draw the same sample again from the same lot.

Pure: a lot size, a policy and a seed in; a plan, a sample and a verdict out.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class Policy:
    """How one category is covered: every item, or a sample."""

    category: str
    mode: str = "full"  # full | sampling
    tolerable_error_percent: float = 5.0
    confidence_percent: float = 95.0
    accept_errors: int = 0
    version: int = 0  # 0: the default, nobody has set a policy for the category

    def check(self) -> None:
        if self.mode not in ("full", "sampling"):
            raise ValueError("a coverage policy is 'full' or 'sampling'")
        if not 0 < self.tolerable_error_percent < 100:
            raise ValueError("the tolerable error rate is a percentage between 0 and 100")
        if not 50 <= self.confidence_percent < 100:
            raise ValueError("the confidence is a percentage from 50 up to, not including, 100")
        if self.accept_errors < 0:
            raise ValueError("the errors accepted in a sample cannot be negative")


@dataclass(frozen=True)
class Plan:
    lot_size: int
    sample_size: int
    accept_errors: int
    # The number of wrong items in a lot at the tolerable error rate, and the chance such a
    # lot would pass this sample: at most 1 - confidence, unless the whole lot is sampled.
    tolerable_errors: int
    pass_probability: float

    @property
    def full(self) -> bool:
        return self.sample_size >= self.lot_size

    def as_json(self) -> dict[str, float | int | bool]:
        return {
            "lot_size": self.lot_size,
            "sample_size": self.sample_size,
            "accept_errors": self.accept_errors,
            "tolerable_errors": self.tolerable_errors,
            "pass_probability": round(self.pass_probability, 6),
            "full": self.full,
        }


def chance_of_passing(lot: int, wrong: int, sample: int, accept: int) -> Fraction:
    """The chance a sample drawn without replacement holds no more than `accept` of the
    lot's `wrong` items: the hypergeometric distribution, exactly."""
    if sample > lot or wrong > lot:
        raise ValueError("a sample and the wrong items are both within the lot")
    total = math.comb(lot, sample)
    passing = sum(
        math.comb(wrong, errors) * math.comb(lot - wrong, sample - errors)
        for errors in range(0, min(accept, wrong, sample) + 1)
        if sample - errors <= lot - wrong
    )
    return Fraction(passing, total)


def plan(lot_size: int, policy: Policy) -> Plan:
    """The smallest sample that catches a lot at the tolerable error rate with the policy's
    confidence. The whole lot when nothing smaller does."""
    policy.check()
    if lot_size <= 0:
        return Plan(0, 0, policy.accept_errors, 0, 1.0)
    rate = Fraction(str(policy.tolerable_error_percent)) / 100
    risk = 1 - Fraction(str(policy.confidence_percent)) / 100
    # The fewest wrong items that put the lot over the tolerable rate.
    wrong = min(lot_size, math.floor(rate * lot_size) + 1)
    for size in range(1, lot_size + 1):
        chance = chance_of_passing(lot_size, wrong, size, policy.accept_errors)
        if chance <= risk:
            return Plan(lot_size, size, policy.accept_errors, wrong, float(chance))
    return Plan(lot_size, lot_size, policy.accept_errors, wrong, 0.0)


def draw(lot: list[str], size: int, seed: int) -> list[str]:
    """`size` of the lot's items, drawn at random from the seed. The lot is put in order
    first, so the same lot and seed give the same sample however the lot was listed."""
    ordered = sorted(lot)
    if size >= len(ordered):
        return ordered
    return sorted(random.Random(seed).sample(ordered, size))  # noqa: S311  # recorded, not secret


@dataclass(frozen=True)
class Verdict:
    reviewed: int
    errors: int
    accept_errors: int
    complete: bool  # every sampled item has been decided
    passed: bool  # complete, with no more errors than accepted
    escalated: bool  # more errors than accepted, whether or not the sample is complete

    @property
    def status(self) -> str:
        if self.escalated:
            return "escalated"
        return "passed" if self.passed else "drawn"


def evaluate(decisions: dict[str, str | None], accept_errors: int) -> Verdict:
    """The verdict on a sample from each sampled item's outcome: `correct` (verified as it
    was proposed), `error` (a person edited or rejected it), or None (not yet decided).

    One error over the accepted number escalates at once: reviewing the rest of the sample
    cannot bring the category back within the plan.
    """
    reviewed = sum(1 for outcome in decisions.values() if outcome is not None)
    errors = sum(1 for outcome in decisions.values() if outcome == "error")
    complete = reviewed == len(decisions)
    escalated = errors > accept_errors
    return Verdict(reviewed, errors, accept_errors, complete, complete and not escalated, escalated)
