"""The Phase 1 KPIs a live bid can show (requirements §14, NFR-14, NFR-15; P1-11).

Accuracy against verified truth (sprinkler counts, pipe length) needs a golden set and comes
from `firebid-eval`. What a live bid measures on its own:

* **False-detection rate:** AI-proposed items a person rejected ÷ AI-proposed items.
* **Missed-item rate:** items a person added by hand ÷ items in the verified takeoff. Only
  what the reviewer noticed: a lower bound on what the platform missed.
* **Duplicates:** groups found, and how many are still unresolved (zero at G1).
* **QTO effort:** workbench time on task (`services.effort`).
* **Escalation rate** and **workflow and tool success:** from agent runs.
* **AI cost:** by route, provider and model, against the bid's budget and the target cost per
  tender.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from firebid.db.models.ai import BidBudget
from firebid.db.models.core import Bid
from firebid.db.models.takeoff import DuplicateGroup, QtoItem
from firebid.db.models.workflow import AgentRun
from firebid.services import effort

CONFIG = Path(__file__).resolve().parents[3] / "config" / "kpi.yaml"
LIVE = ("proposed", "edited", "verified", "baselined", "rejected")


@cache
def settings() -> dict[str, Any]:
    return dict(yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {})


def target_cost_per_tender() -> Decimal | None:
    value = settings().get("target_cost_per_tender_sgd")
    return Decimal(str(value)) if value is not None else None


@dataclass
class AgentStats:
    runs: int = 0
    succeeded: int = 0
    failed: int = 0
    escalated: int = 0
    cost_sgd: Decimal = Decimal("0")
    by_model: list[dict[str, Any]] = field(default_factory=list)

    @property
    def finished(self) -> int:
        return self.succeeded + self.failed + self.escalated

    @property
    def escalation_rate(self) -> float | None:
        return self.escalated / self.finished if self.finished else None

    @property
    def success_rate(self) -> float | None:
        """Runs that completed without failure. An escalation is not a failure: it is the
        runtime handing the work to a person, as designed."""
        return (self.succeeded + self.escalated) / self.finished if self.finished else None


def agent_stats(session: Session, bid_id: uuid.UUID | None = None) -> AgentStats:
    query = select(
        AgentRun.route,
        AgentRun.provider,
        AgentRun.model,
        AgentRun.state,
        func.count(),
        func.coalesce(func.sum(AgentRun.cost_sgd), 0),
    ).group_by(AgentRun.route, AgentRun.provider, AgentRun.model, AgentRun.state)
    if bid_id is not None:
        query = query.where(AgentRun.bid_id == bid_id)
    out = AgentStats()
    grouped: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"runs": 0, "cost_sgd": Decimal("0")}
    )
    for route, provider, model, state, count, cost in session.execute(query).tuples():
        out.runs += count
        if state == "succeeded":
            out.succeeded += count
        elif state == "failed":
            out.failed += count
        elif state == "escalated":
            out.escalated += count
        out.cost_sgd += Decimal(cost or 0)
        row = grouped[(route, provider or "-", model or "-")]
        row["runs"] += count
        row["cost_sgd"] += Decimal(cost or 0)
    out.by_model = [
        {"route": r, "provider": p, "model": m, **values}
        for (r, p, m), values in sorted(grouped.items(), key=lambda kv: -kv[1]["cost_sgd"])
    ]
    return out


@dataclass
class BidKpis:
    bid_id: uuid.UUID
    human_id: str
    ai_items: int
    rejected_ai_items: int
    manual_items: int
    verified_items: int
    duplicate_groups: int
    unresolved_duplicates: int
    minutes_on_task: int
    agents: AgentStats
    budget_sgd: Decimal | None
    target_cost_sgd: Decimal | None

    @property
    def false_detection_rate(self) -> float | None:
        return self.rejected_ai_items / self.ai_items if self.ai_items else None

    @property
    def missed_item_rate(self) -> float | None:
        return self.manual_items / self.verified_items if self.verified_items else None


def bid_kpis(session: Session, bid: Bid) -> BidKpis:
    counts: dict[tuple[bool, str], int] = {
        (is_manual, state): n
        for is_manual, state, n in session.execute(
            select(QtoItem.is_manual, QtoItem.state, func.count())
            .where(QtoItem.bid_id == bid.id, QtoItem.state.in_(LIVE))
            .group_by(QtoItem.is_manual, QtoItem.state)
        ).tuples()
    }
    ai_items = sum(n for (manual, _), n in counts.items() if not manual)
    rejected = sum(n for (manual, state), n in counts.items() if not manual and state == "rejected")
    manual = sum(n for (is_manual, state), n in counts.items() if is_manual and state != "rejected")
    verified = sum(n for (_, state), n in counts.items() if state in ("verified", "baselined"))
    groups = dict(
        session.execute(
            select(DuplicateGroup.status, func.count())
            .where(DuplicateGroup.bid_id == bid.id)
            .group_by(DuplicateGroup.status)
        )
        .tuples()
        .all()
    )
    budget = session.execute(
        select(BidBudget.limit_sgd).where(BidBudget.bid_id == bid.id)
    ).scalar_one_or_none()
    return BidKpis(
        bid_id=bid.id,
        human_id=bid.human_id,
        ai_items=ai_items,
        rejected_ai_items=rejected,
        manual_items=manual,
        verified_items=verified,
        duplicate_groups=sum(groups.values()),
        unresolved_duplicates=groups.get("unresolved", 0),
        minutes_on_task=effort.time_on_task(session, bid.id).minutes,
        agents=agent_stats(session, bid.id),
        budget_sgd=budget,
        target_cost_sgd=target_cost_per_tender(),
    )
