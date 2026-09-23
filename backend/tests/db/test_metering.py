"""Cost attribution and budgets (FR-ADM-05, NFR-15)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.ai_gateway.config import ModelConfig, Price
from firebid.ai_gateway.metering import BudgetExceeded, CallContext, PostgresMeter, cost_of
from firebid.ai_gateway.types import GenerationResponse, StopReason, Usage
from firebid.db.models.ai import BidBudget
from firebid.db.models.core import Bid
from firebid.db.models.workflow import AgentRun
from firebid.notifications import RecordingNotifier

PRICED = ModelConfig(
    provider="anthropic",
    model_id="claude-opus-5",
    prices=[
        Price(
            effective_from=date(2026, 1, 1),
            input_per_mtok=5.0,
            output_per_mtok=25.0,
            cached_input_per_mtok=0.5,
        )
    ],
)


def answer(input_tokens: int = 1_000_000, output_tokens: int = 0, cached: int = 0):  # type: ignore[no-untyped-def]
    return GenerationResponse(
        text="ok",
        stop_reason=StopReason.END,
        provider="anthropic",
        model="claude-opus-5",
        usage=Usage(
            input_tokens=input_tokens, output_tokens=output_tokens, cached_input_tokens=cached
        ),
    )


@pytest.mark.req("FR-ADM-05")
class TestPricing:
    def test_a_million_input_tokens_costs_the_input_rate(self) -> None:
        assert cost_of(PRICED, Usage(input_tokens=1_000_000)) == Decimal("5.000000")

    def test_output_is_priced_separately(self) -> None:
        assert cost_of(PRICED, Usage(output_tokens=1_000_000)) == Decimal("25.000000")

    def test_cached_input_is_cheaper_and_not_double_counted(self) -> None:
        """Cached tokens appear inside input_tokens, so they must not be charged twice."""
        usage = Usage(input_tokens=1_000_000, cached_input_tokens=1_000_000)
        assert cost_of(PRICED, usage) == Decimal("0.500000")

    def test_without_a_cached_rate_cached_tokens_cost_the_input_rate(self) -> None:
        """Not free: assuming free would understate a real bill."""
        model = ModelConfig(
            provider="p",
            model_id="m",
            prices=[
                Price(effective_from=date(2026, 1, 1), input_per_mtok=5.0, output_per_mtok=1.0)
            ],
        )
        usage = Usage(input_tokens=1_000_000, cached_input_tokens=1_000_000)
        assert cost_of(model, usage) == Decimal("5.000000")

    def test_the_price_of_the_day_is_used(self) -> None:
        model = ModelConfig(
            provider="p",
            model_id="m",
            prices=[
                Price(effective_from=date(2026, 1, 1), input_per_mtok=5.0, output_per_mtok=1.0),
                Price(effective_from=date(2026, 6, 1), input_per_mtok=4.0, output_per_mtok=1.0),
            ],
        )
        usage = Usage(input_tokens=1_000_000)
        assert cost_of(model, usage, on=date(2026, 3, 1)) == Decimal("5.000000")
        assert cost_of(model, usage, on=date(2026, 9, 1)) == Decimal("4.000000")

    def test_an_unpriced_model_is_zero_rather_than_a_guess(self) -> None:
        unpriced = ModelConfig(provider="p", model_id="m")
        assert cost_of(unpriced, Usage(input_tokens=1_000_000)) == Decimal(0)


@pytest.mark.req("FR-ADM-05")
class TestRecording:
    def test_a_call_is_attributed_to_its_bid_route_provider_and_model(
        self, session: Session, bid: Bid
    ) -> None:
        meter = PostgresMeter(session)
        meter.record(
            "title_block_read",
            PRICED,
            answer(input_tokens=1_000_000),
            latency_ms=1234,
            context=CallContext(bid_id=bid.id, agent="title_block_reader"),
            config_version="cfg1",
        )
        session.commit()

        run = session.execute(select(AgentRun)).scalar_one()
        assert run.bid_id == bid.id
        assert run.route == "title_block_read"
        assert run.agent == "title_block_reader"
        assert run.provider == "anthropic"
        assert run.model == "claude-opus-5"
        assert run.config_version == "cfg1"
        assert run.latency_ms == 1234
        assert run.cost_sgd == Decimal("5.000000")

    def test_a_cache_hit_is_recorded_at_no_cost(self, session: Session, bid: Bid) -> None:
        """Otherwise the saving is invisible and the bill is overstated."""
        from dataclasses import replace

        meter = PostgresMeter(session)
        meter.record(
            "work",
            PRICED,
            replace(answer(input_tokens=1_000_000), cache_hit=True),
            latency_ms=0,
            context=CallContext(bid_id=bid.id),
            config_version="cfg1",
        )
        session.commit()

        run = session.execute(select(AgentRun)).scalar_one()
        assert run.cost_sgd == Decimal(0)
        assert run.cache_hit is True

    def test_several_calls_in_one_run_accumulate_onto_that_run(
        self, session: Session, bid: Bid
    ) -> None:
        run_id = uuid.uuid4()
        session.add(AgentRun(id=run_id, bid_id=bid.id, route="work", state="running"))
        session.commit()

        meter = PostgresMeter(session)
        context = CallContext(bid_id=bid.id, run_id=run_id, agent="a")
        for _ in range(3):
            meter.record("work", PRICED, answer(input_tokens=1_000_000), 10, context, "cfg1")
        session.commit()

        runs = session.execute(select(AgentRun)).scalars().all()
        assert len(runs) == 1, "an agent run should be one record, not one per call"
        assert runs[0].cost_sgd == Decimal("15.000000")


@pytest.mark.req("NFR-15")
class TestBudgets:
    def _budget(self, session: Session, bid: Bid, limit: str, **changes: object) -> BidBudget:
        budget = BidBudget(
            bid_id=bid.id,
            limit_sgd=Decimal(limit),
            alert_at=changes.get("alert_at", [0.5, 0.8, 1.0]),
            alerts_sent=[],
            owner_email=changes.get("owner_email", "manager@firebid.test"),
        )
        session.add(budget)
        session.commit()
        return budget

    def test_a_bid_within_its_budget_may_call(self, session: Session, bid: Bid) -> None:
        self._budget(session, bid, "100.00")
        PostgresMeter(session).check(CallContext(bid_id=bid.id))  # does not raise

    def test_a_bid_that_has_spent_its_budget_is_refused(self, session: Session, bid: Bid) -> None:
        self._budget(session, bid, "10.00")
        meter = PostgresMeter(session)
        meter.record(
            "work", PRICED, answer(input_tokens=3_000_000), 10, CallContext(bid_id=bid.id), "c"
        )
        session.commit()

        with pytest.raises(BudgetExceeded) as caught:
            meter.check(CallContext(bid_id=bid.id))
        assert caught.value.scope == "bid"
        assert caught.value.spent == Decimal("15.000000")

    def test_a_bid_with_no_budget_is_unlimited(self, session: Session, bid: Bid) -> None:
        PostgresMeter(session).check(CallContext(bid_id=bid.id))  # no budget row at all

    def test_crossing_a_threshold_warns_the_owner_once(self, session: Session, bid: Bid) -> None:
        self._budget(session, bid, "10.00")
        notifier = RecordingNotifier()
        meter = PostgresMeter(session, notifier=notifier)
        context = CallContext(bid_id=bid.id)

        # 5 SGD of a 10 SGD budget crosses the 0.5 threshold.
        meter.record("work", PRICED, answer(input_tokens=1_000_000), 10, context, "c")
        session.commit()
        assert len(notifier.sent) == 1
        assert "50%" in notifier.sent[0].subject

        # A further call below the next threshold does not warn again.
        meter.record("work", PRICED, answer(input_tokens=200_000), 10, context, "c")
        session.commit()
        assert len(notifier.sent) == 1, "a threshold should warn once, not on every call"

    def test_each_threshold_warns_in_turn(self, session: Session, bid: Bid) -> None:
        self._budget(session, bid, "10.00")
        notifier = RecordingNotifier()
        meter = PostgresMeter(session, notifier=notifier)
        context = CallContext(bid_id=bid.id)

        for _ in range(2):  # 5 SGD, then 10 SGD
            meter.record("work", PRICED, answer(input_tokens=1_000_000), 10, context, "c")
            session.commit()

        assert [n.subject for n in notifier.sent] == [
            "AI spend on this bid has reached 50% of its budget",
            "AI spend on this bid has reached 100% of its budget",
        ]

    def test_exceeding_a_run_budget_pauses_the_run_and_alerts_the_owner(
        self, session: Session, bid: Bid
    ) -> None:
        run_id = uuid.uuid4()
        session.add(AgentRun(id=run_id, bid_id=bid.id, route="work", state="running"))
        session.commit()

        notifier = RecordingNotifier()
        meter = PostgresMeter(session, notifier=notifier)
        context = CallContext(
            bid_id=bid.id,
            run_id=run_id,
            agent="title_block_reader",
            run_budget_sgd=Decimal("1.00"),
            owner_email="estimator@firebid.test",
        )

        meter.record("work", PRICED, answer(input_tokens=1_000_000), 10, context, "c")
        session.commit()

        with pytest.raises(BudgetExceeded) as caught:
            meter.check(context)
        session.commit()

        assert caught.value.scope == "run"
        run = session.get(AgentRun, run_id)
        assert run is not None
        assert run.state == "escalated", "an over-budget run must stop, not keep spending"
        assert run.error_type == "run_budget_exceeded"
        assert notifier.sent[-1].recipient_email == "estimator@firebid.test"
        assert "paused" in notifier.sent[-1].subject
