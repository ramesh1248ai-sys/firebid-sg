"""Sampling mode for item categories whose accuracy is proven (FR-REV-05).

* **Policy.** Each item category (an item's classification: sprinkler, branch, valve ...) is
  covered in full unless the organisation's policy for it says a sample. The policy is
  versioned and set by a Senior Estimator; every change is audited, with the accuracy
  evidence it rests on in its note.
* **Draw.** For a category under sampling, a sample of a bid's items is drawn at random to
  the plan `qto.sampling` works out from the lot's size and the policy. The lot, the sample
  and the seed are recorded.
* **Verdict.** A sampled item verified as it was proposed is correct; one a person edited or
  rejected is an error. More errors than the plan accepts and the sample is escalated: the
  category is in full review on that bid from then on, and no later sample is drawn for it.
* **Acceptance.** Once every sampled item is decided within the plan, a Senior Estimator
  accepts the category on the sample: the lot's remaining items are verified by that named
  action, each recording the sample it was accepted on. Items that joined the category
  after the draw are not in the lot and are reviewed one by one.

G1's rule is unchanged: every item is verified. What the policy changes is how the items of
a category may come to be verified, and the coverage report says which way each was.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.audit import record_event
from firebid.db.models.baseline import CoveragePolicy, ReviewSample
from firebid.db.models.core import Bid
from firebid.db.models.takeoff import QtoItem
from firebid.domain.actors import Actor, AuditContext
from firebid.qto import sampling
from firebid.qto.sampling import Policy
from firebid.services import qto

log = structlog.get_logger("firebid.sampling")

DONE = ("verified", "baselined")
ACCEPTED = "accepted_on_sample"


class SamplingError(ValueError):
    """A sampling request that cannot be done, with the reason a person can act on."""


def category_of(item: QtoItem) -> str:
    return item.classification or item.item_type


# --- Policy ---------------------------------------------------------------------------------


def _policy(row: CoveragePolicy) -> Policy:
    return Policy(
        category=row.category,
        mode=row.mode,
        tolerable_error_percent=float(row.tolerable_error_percent),
        confidence_percent=float(row.confidence_percent),
        accept_errors=row.accept_errors,
        version=row.version,
    )


def policy_rows(session: Session, organisation_id: uuid.UUID) -> dict[str, CoveragePolicy]:
    """Each category's policy in force. A category with none is in full review."""
    return {
        row.category: row
        for row in session.execute(
            select(CoveragePolicy).where(
                CoveragePolicy.organisation_id == organisation_id,
                CoveragePolicy.retired_at.is_(None),
            )
        ).scalars()
    }


def policy_for(session: Session, organisation_id: uuid.UUID, category: str) -> Policy:
    row = policy_rows(session, organisation_id).get(category)
    return _policy(row) if row else Policy(category)


def set_policy(
    session: Session,
    organisation_id: uuid.UUID,
    category: str,
    *,
    mode: str,
    actor: Actor,
    tolerable_error_percent: float = 5.0,
    confidence_percent: float = 95.0,
    accept_errors: int = 0,
    note: str | None = None,
) -> CoveragePolicy:
    """A new version of a category's policy. The one in force is retired, never changed."""
    category = category.strip()
    if not category:
        raise SamplingError("a policy is for a named item category")
    wanted = Policy(category, mode, tolerable_error_percent, confidence_percent, accept_errors)
    try:
        wanted.check()
    except ValueError as refusal:
        raise SamplingError(str(refusal)) from refusal
    if mode == "sampling" and not (note or "").strip():
        raise SamplingError(
            "say what accuracy evidence puts this category under sampling (the note)"
        )
    current = policy_rows(session, organisation_id).get(category)
    now = datetime.now(UTC)
    if current is not None:
        current.retired_at = now
        session.flush()
    row = CoveragePolicy(
        organisation_id=organisation_id,
        category=category,
        version=(current.version + 1) if current else 1,
        mode=mode,
        tolerable_error_percent=Decimal(str(tolerable_error_percent)),
        confidence_percent=Decimal(str(confidence_percent)),
        accept_errors=accept_errors,
        note=note,
        created_by_id=actor.id,
    )
    session.add(row)
    session.flush()
    record_event(
        session,
        context=AuditContext(organisation_id=organisation_id),
        actor=actor,
        action="coverage policy: new version",
        entity_type=CoveragePolicy.__tablename__,
        entity_id=row.id,
        before=_state(current) if current else None,
        after=_state(row),
        reason=note,
    )
    return row


def _state(row: CoveragePolicy) -> dict[str, Any]:
    return {
        "category": row.category,
        "version": row.version,
        "mode": row.mode,
        # Written the same whether the row was just made or read back: 5, not 5.000.
        "tolerable_error_percent": format(Decimal(row.tolerable_error_percent).normalize(), "f"),
        "confidence_percent": format(Decimal(row.confidence_percent).normalize(), "f"),
        "accept_errors": row.accept_errors,
    }


# --- Samples --------------------------------------------------------------------------------


def _lot(session: Session, bid_id: uuid.UUID, category: str) -> list[QtoItem]:
    """The category's items on the bid that a person may verify: live, and not rejected."""
    return [
        item
        for item in qto.live_items(session, bid_id)
        if category_of(item) == category and item.state != "rejected"
    ]


def latest(session: Session, bid_id: uuid.UUID, category: str) -> ReviewSample | None:
    return (
        session.execute(
            select(ReviewSample)
            .where(ReviewSample.bid_id == bid_id, ReviewSample.category == category)
            .order_by(ReviewSample.created_at.desc(), ReviewSample.id)
        )
        .scalars()
        .first()
    )


def escalated(session: Session, bid_id: uuid.UUID, category: str) -> ReviewSample | None:
    """The sample that sent the category back to full review on this bid, if one did."""
    return (
        session.execute(
            select(ReviewSample).where(
                ReviewSample.bid_id == bid_id,
                ReviewSample.category == category,
                ReviewSample.status == "escalated",
            )
        )
        .scalars()
        .first()
    )


def outcome_of(item: QtoItem | None) -> str | None:
    """`correct`, `error`, or None while a sampled item is still to be decided."""
    if item is None:
        return None
    if item.state == "rejected":
        return "error"
    if item.state not in DONE:
        return None
    return "error" if dict(item.derivation or {}).get("edit") else "correct"


def draw(
    session: Session, bid: Bid, category: str, actor: Actor, *, seed: int | None = None
) -> ReviewSample:
    """Draw and record a sample of the category's items on the bid."""
    policy = policy_for(session, bid.organisation_id, category)
    if policy.mode != "sampling":
        raise SamplingError(f"'{category}' is in full review: its policy is not sampling")
    if escalated(session, bid.id, category) is not None:
        raise SamplingError(
            f"a sample of '{category}' on this bid exceeded its error threshold: "
            "the category is in full review here"
        )
    current = latest(session, bid.id, category)
    if current is not None and current.status in ("drawn", "passed"):
        raise SamplingError(
            f"a sample of '{category}' is already drawn: review it, or accept the category on it"
        )
    lot = _lot(session, bid.id, category)
    waiting = [item for item in lot if item.state not in DONE]
    if not waiting:
        raise SamplingError(f"every '{category}' item on this bid is already verified")
    plan = sampling.plan(len(lot), policy)
    used = secrets.randbits(62) if seed is None else seed
    chosen = sampling.draw([item.human_id for item in lot], plan.sample_size, used)
    row = ReviewSample(
        bid_id=bid.id,
        category=category,
        policy_version=policy.version,
        plan={
            **plan.as_json(),
            "tolerable_error_percent": policy.tolerable_error_percent,
            "confidence_percent": policy.confidence_percent,
        },
        lot=sorted(item.human_id for item in lot),
        sample=chosen,
        seed=str(used),
        status="drawn",
        drawn_by_id=actor.id,
        drawn_by=actor.label,
    )
    session.add(row)
    session.flush()
    record_event(
        session,
        context=AuditContext(organisation_id=bid.organisation_id, bid_id=bid.id),
        actor=actor,
        action="review sample: drawn",
        entity_type=ReviewSample.__tablename__,
        entity_id=row.id,
        after={
            "category": category,
            "lot_size": len(lot),
            "sample": chosen,
            "seed": row.seed,
            "policy_version": policy.version,
        },
    )
    refresh(session, row)
    return row


def refresh(session: Session, sample: ReviewSample) -> ReviewSample:
    """Bring a sample's outcomes and status up to what has been decided since."""
    if sample.status in ("accepted", "escalated"):
        return sample
    live = {item.human_id: item for item in qto.live_items(session, sample.bid_id)}
    decisions: dict[str, Any] = {
        human_id: outcome_of(live.get(human_id)) for human_id in sample.sample
    }
    verdict = sampling.evaluate(decisions, int(sample.plan.get("accept_errors", 0)))  # type: ignore[call-overload]
    if dict(sample.outcomes or {}) != decisions:
        sample.outcomes = dict(decisions)
    if sample.errors != verdict.errors:
        sample.errors = verdict.errors
    if sample.status != verdict.status:
        sample.status = verdict.status
        if verdict.escalated:
            sample.decided_at = datetime.now(UTC)
            log.info(
                "sample_escalated",
                bid_id=str(sample.bid_id),
                category=sample.category,
                errors=verdict.errors,
            )
    session.flush()
    return sample


def accept_on_sample(session: Session, bid: Bid, category: str, actor: Actor) -> ReviewSample:
    """Accept the category on its sample: verify the lot's remaining items by this action."""
    sample = latest(session, bid.id, category)
    if sample is None:
        raise SamplingError(f"no sample of '{category}' has been drawn on this bid")
    refresh(session, sample)
    if sample.status == "escalated":
        raise SamplingError(
            f"the sample found {sample.errors} error(s), more than the "
            f"{sample.plan.get('accept_errors', 0)} accepted: '{category}' is in full review"
        )
    if sample.status == "accepted":
        raise SamplingError(f"'{category}' was already accepted on this sample")
    if sample.status != "passed":
        left = sum(1 for outcome in dict(sample.outcomes).values() if outcome is None)
        raise SamplingError(f"{left} sampled item(s) are still to be reviewed")
    lot = set(sample.lot)
    now = datetime.now(UTC)
    accepted = []
    for item in _lot(session, bid.id, category):
        if item.human_id not in lot or item.state in DONE:
            continue
        qto.verify(
            session,
            item,
            actor,
            f"accepted on a sample of {len(sample.sample)} of {len(sample.lot)} "
            f"'{category}' items with {sample.errors} error(s)",
        )
        item.derivation = {
            **dict(item.derivation or {}),
            ACCEPTED: {"sample_id": str(sample.id), "by": actor.label, "at": now.isoformat()},
        }
        accepted.append(item.human_id)
    sample.status = "accepted"
    sample.decided_at = now
    sample.decided_by = actor.label
    session.flush()
    record_event(
        session,
        context=AuditContext(organisation_id=bid.organisation_id, bid_id=bid.id),
        actor=actor,
        action="review sample: category accepted",
        entity_type=ReviewSample.__tablename__,
        entity_id=sample.id,
        after={"category": category, "accepted_items": accepted, "errors": sample.errors},
    )
    return sample


def summary(session: Session, bid: Bid) -> list[dict[str, Any]]:
    """Each category on the bid: its policy, how many items are verified and how, and where
    its sample stands. What the coverage report shows beside the totals."""
    policies = policy_rows(session, bid.organisation_id)
    by_category: dict[str, list[QtoItem]] = {}
    for item in qto.live_items(session, bid.id):
        if item.state != "rejected":
            by_category.setdefault(category_of(item), []).append(item)
    out = []
    for category, items in sorted(by_category.items()):
        row = policies.get(category)
        policy = _policy(row) if row else Policy(category)
        sample = latest(session, bid.id, category)
        if sample is not None:
            refresh(session, sample)
        verified = [item for item in items if item.state in DONE]
        on_sample = sum(1 for item in verified if dict(item.derivation or {}).get(ACCEPTED))
        full_review = policy.mode == "full" or (sample is not None and sample.status == "escalated")
        out.append(
            {
                "category": category,
                "mode": "full" if full_review else "sampling",
                "policy_mode": policy.mode,
                "policy_version": policy.version,
                "items": len(items),
                "verified": len(verified),
                "verified_individually": len(verified) - on_sample,
                "accepted_on_sample": on_sample,
                "met": len(verified) == len(items),
                "sample": sample_json(sample) if sample else None,
            }
        )
    return out


def sample_json(sample: ReviewSample) -> dict[str, Any]:
    return {
        "id": str(sample.id),
        "category": sample.category,
        "status": sample.status,
        "policy_version": sample.policy_version,
        "plan": dict(sample.plan),
        "lot": list(sample.lot),
        "sample": list(sample.sample),
        "seed": sample.seed,
        "errors": sample.errors,
        "outcomes": dict(sample.outcomes or {}),
        "drawn_by": sample.drawn_by,
        "drawn_at": sample.created_at,
        "decided_by": sample.decided_by,
        "decided_at": sample.decided_at,
    }
