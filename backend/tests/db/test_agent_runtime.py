"""The agent contract and runtime (requirements §8.2, §8.4; ADR-007)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from firebid.agents.base import (
    AgentInput,
    AgentResult,
    AgentTool,
    Assertion,
    AutonomyLevel,
    AutonomyRefused,
    ToolRegistry,
)
from firebid.agents.runtime import Escalated, complete_task_and_continue, run_agent
from firebid.agents.title_block import TitleBlock, TitleBlockInput, TitleBlockReader
from firebid.ai_gateway.config import load_config
from firebid.ai_gateway.errors import NoModelAvailable, ProviderUnavailable
from firebid.ai_gateway.providers.fake import FakeAdapter
from firebid.ai_gateway.router import Router
from firebid.db.models.core import Bid
from firebid.db.models.workflow import AgentRun, HumanTask

PNG = b"\x89PNG\r\n\x1a\n"

CONFIG = """
version: 1
providers:
  primary:
    kind: fake
    approved_data_classes: [internal, confidential]
models:
  first:
    provider: primary
    model_id: first-1
    capabilities: [vision, structured_output]
routes:
  title_block_read:
    requires: [vision, structured_output]
    data_class: confidential
    models: [first]
    reasoning: low
"""


# Builds a router whose fake provider is scripted with the given replies, in order.
BuildRouter = Callable[..., tuple[Router, FakeAdapter]]


@pytest.fixture
def router(tmp_path: Path) -> BuildRouter:
    path = tmp_path / "llm.yaml"
    path.write_text(CONFIG, encoding="utf-8")

    def build(*replies: str) -> tuple[Router, FakeAdapter]:
        adapter = FakeAdapter("primary")
        for reply in replies:
            adapter.reply(reply)
        return (
            Router(
                config=load_config(path),
                adapters={"primary": adapter},
                backoff_base_seconds=0,
                sleep=lambda _s: None,
            ),
            adapter,
        )

    return build


def ask(bid: Bid, key: str = "run-1", **changes: object) -> AgentInput:
    return AgentInput(
        bid_id=bid.id,
        idempotency_key=key,
        payload=TitleBlockInput(image_png=PNG, **changes),
    )


CONFIDENT = (
    '{"sheet_number": "FP-L05-201", "revision": "R04", "sheet_title": "Level 5 fire protection",'
    ' "scale": "1:100", "sheet_number_confidence": 0.97, "revision_confidence": 0.95,'
    ' "sheet_title_confidence": 0.9, "scale_confidence": 0.9}'
)
UNSURE = (
    '{"sheet_number": "FP-L05-201", "revision": "R04",'
    ' "sheet_number_confidence": 0.41, "revision_confidence": 0.95}'
)


@pytest.mark.req("FR-ADM-01")
class TestAutonomyLevels:
    def test_a_commit_level_tool_is_refused_at_registration(self) -> None:
        """L3 must fail when the tool is written, not when it fires in front of a client."""
        registry = ToolRegistry()
        with pytest.raises(AutonomyRefused, match="never permitted"):
            registry.register(
                AgentTool(
                    name="submit_to_scdf",
                    description="Submit the plan to the authority.",
                    level=AutonomyLevel.COMMIT,
                )
            )
        assert len(registry) == 0

    @pytest.mark.parametrize(
        "level", [AutonomyLevel.INFORM, AutonomyLevel.DRAFT, AutonomyLevel.BOUNDED_ACTION]
    )
    def test_the_permitted_levels_register(self, level: AutonomyLevel) -> None:
        registry = ToolRegistry()
        registry.register(AgentTool(name="t", description="d", level=level))
        assert "t" in registry

    def test_the_demonstration_agent_is_draft_only(self, router: BuildRouter) -> None:
        """Everything it produces is a proposal a person confirms."""
        gateway, _ = router()
        agent = TitleBlockReader(gateway)
        assert agent.tools.get("read_title_block").level is AutonomyLevel.DRAFT


@pytest.mark.req("NFR-11")
class TestRunningAnAgent:
    def test_a_confident_run_succeeds_and_records_its_provenance(
        self, session: Session, bid: Bid, router: BuildRouter
    ) -> None:
        gateway, _ = router(CONFIDENT)
        run = run_agent(session, TitleBlockReader(gateway), ask(bid))
        session.commit()

        assert run.state == "succeeded"
        assert run.agent == "title_block_reader"
        assert run.route == "title_block_read"
        assert run.provider == "primary"
        assert run.model == "first-1"
        assert run.finished_at is not None

    def test_re_running_the_same_key_proposes_nothing_twice(
        self, session: Session, bid: Bid, router: BuildRouter
    ) -> None:
        """Jobs are delivered at least once, so the second delivery must be a no-op."""
        gateway, adapter = router(CONFIDENT, CONFIDENT)
        agent = TitleBlockReader(gateway)

        first = run_agent(session, agent, ask(bid, key="same"))
        session.commit()
        second = run_agent(session, agent, ask(bid, key="same"))
        session.commit()

        assert first.id == second.id
        assert len(adapter.calls) == 1, "the model was asked twice for the same work"
        assert session.execute(select(func.count()).select_from(AgentRun)).scalar() == 1

    def test_a_different_key_is_a_different_run(
        self, session: Session, bid: Bid, router: BuildRouter
    ) -> None:
        gateway, _ = router(CONFIDENT, CONFIDENT)
        agent = TitleBlockReader(gateway)
        run_agent(session, agent, ask(bid, key="one"))
        run_agent(session, agent, ask(bid, key="two"))
        session.commit()
        assert session.execute(select(func.count()).select_from(AgentRun)).scalar() == 2


@pytest.mark.req("FR-ADM-01")
class TestEscalation:
    def test_low_confidence_raises_a_task_rather_than_guessing(
        self, session: Session, bid: Bid, router: BuildRouter
    ) -> None:
        gateway, _ = router(UNSURE)
        with pytest.raises(Escalated) as caught:
            run_agent(session, TitleBlockReader(gateway), ask(bid))
        session.commit()

        task = caught.value.task
        assert task.state == "open"
        assert "sheet_number" in task.title
        assert task.payload["agent"] == "title_block_reader"
        # The person is given the reading to check, not an empty task.
        assert task.payload["assertions"]

        run = session.execute(select(AgentRun)).scalar_one()
        assert run.state == "escalated"
        assert run.error_type == "low_confidence"

    def test_an_absent_field_is_no_confidence_not_a_confident_blank(
        self, session: Session, bid: Bid, router: BuildRouter
    ) -> None:
        """ "The drawing does not say" and "we did not read it" are different problems."""
        gateway, _ = router('{"revision": "R04", "revision_confidence": 0.99}')
        with pytest.raises(Escalated) as caught:
            run_agent(session, TitleBlockReader(gateway), ask(bid))
        session.commit()
        assert "sheet_number" in caught.value.task.title

    def test_every_model_failing_raises_a_task_with_the_reason(
        self, session: Session, bid: Bid, router: BuildRouter
    ) -> None:
        failure = ProviderUnavailable("503", provider="primary", status=503)
        gateway, _ = router()
        gateway._adapters["primary"].queue(failure, failure, failure)  # type: ignore[attr-defined]

        with pytest.raises(Escalated) as caught:
            run_agent(session, TitleBlockReader(gateway), ask(bid))
        session.commit()

        run = session.execute(select(AgentRun)).scalar_one()
        assert run.state == "escalated"
        assert run.error_type == NoModelAvailable.__name__
        assert "could not complete" in caught.value.task.title

    def test_an_output_that_does_not_fit_the_schema_escalates(
        self, session: Session, bid: Bid, router: BuildRouter
    ) -> None:
        gateway, _ = router("this is not JSON", "still not JSON")
        with pytest.raises(Escalated):
            run_agent(session, TitleBlockReader(gateway), ask(bid))
        session.commit()
        assert session.execute(select(AgentRun)).scalar_one().state == "escalated"

    def test_the_task_carries_its_continuation(
        self, session: Session, bid: Bid, router: BuildRouter
    ) -> None:
        gateway, _ = router(UNSURE)
        with pytest.raises(Escalated) as caught:
            run_agent(session, TitleBlockReader(gateway), ask(bid))
        session.commit()
        assert caught.value.task.continuation_task == "agents.title_block_reader.continue"


@pytest.mark.req("FR-ADM-01")
class TestCompletingATask:
    def test_completing_queues_the_continuation_in_the_same_transaction(
        self, session: Session, bid: Bid
    ) -> None:
        """A task marked done whose continuation was never queued is work that has stopped."""
        task = HumanTask(
            bid_id=bid.id,
            kind="agent.demo",
            title="check it",
            state="open",
            continuation_task="agents.demo.continue",
        )
        session.add(task)
        session.commit()

        queued: list[tuple[str, dict[str, object]]] = []

        def enqueue(_session: Session, name: str, payload: dict[str, object]) -> None:
            queued.append((name, payload))

        completed = complete_task_and_continue(session, task.id, None, enqueue)
        session.commit()

        assert completed.state == "done"
        assert completed.completed_at is not None
        assert queued == [("agents.demo.continue", {"task_id": str(task.id)})]

    def test_completing_twice_queues_nothing_a_second_time(
        self, session: Session, bid: Bid
    ) -> None:
        task = HumanTask(
            bid_id=bid.id,
            kind="agent.demo",
            title="check it",
            state="open",
            continuation_task="agents.demo.continue",
        )
        session.add(task)
        session.commit()

        queued: list[tuple[str, dict[str, object]]] = []

        def enqueue(_session: Session, name: str, payload: dict[str, object]) -> None:
            queued.append((name, payload))

        complete_task_and_continue(session, task.id, None, enqueue)
        complete_task_and_continue(session, task.id, None, enqueue)
        session.commit()
        assert len(queued) == 1

    def test_an_unknown_task_is_refused(self, session: Session) -> None:
        with pytest.raises(LookupError):
            complete_task_and_continue(session, uuid.uuid4(), None, None)


@pytest.mark.req("NFR-10")
def test_assertions_carry_a_confidence_per_field(
    session: Session, bid: Bid, router: BuildRouter
) -> None:
    """An estimator must be able to ask "why do you think that" per field, not per answer."""
    gateway, _ = router(CONFIDENT)
    result = TitleBlockReader(gateway).run(ask(bid))
    by_field = {a.field: a for a in result.assertions}
    assert by_field["sheet_number"].confidence == pytest.approx(0.97)
    assert by_field["revision"].confidence == pytest.approx(0.95)
    assert isinstance(result.output, TitleBlock)
    assert result.output.sheet_number == "FP-L05-201"


class Shape(BaseModel):
    value: int


def test_an_agent_result_reports_which_assertions_are_uncertain() -> None:
    result: AgentResult[Shape] = AgentResult(
        output=Shape(value=1),
        assertions=[
            Assertion(field="a", value="x", confidence=0.9),
            Assertion(field="b", value="y", confidence=0.4),
        ],
    )
    assert [a.field for a in result.low_confidence(0.85)] == ["b"]
