"""Running an agent: once, idempotently, and never silently.

Every agent run is a job on the PostgreSQL queue (ADR-007). This wrapper is what turns a
typed function into something safe to run repeatedly in a system where jobs are delivered at
least once:

* An `AgentRun` is opened on the idempotency key. A second job with the same key finds the
  finished run and returns it rather than proposing the same thing twice.
* Low confidence, invalid output or an exhausted retry becomes a `HumanTask` carrying the
  context a person needs to finish the job — not a guess written to the bid.
* Every run records provider, model, prompt version and configuration version, so an answer
  can be explained months later.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.agents.base import Agent, AgentInput, AgentResult
from firebid.ai_gateway.errors import GatewayError, NoModelAvailable
from firebid.ai_gateway.metering import BudgetExceeded
from firebid.db.models.workflow import AgentRun, HumanTask

log = structlog.get_logger("firebid.agents")


class Escalated(Exception):
    """The agent stopped and asked for a person. Carries the task it raised."""

    def __init__(self, reason: str, task: HumanTask) -> None:
        super().__init__(reason)
        self.reason = reason
        self.task = task


def existing_run(session: Session, idempotency_key: str) -> AgentRun | None:
    return session.execute(
        select(AgentRun).where(AgentRun.idempotency_key == idempotency_key)
    ).scalar_one_or_none()


def run_agent(
    session: Session,
    agent: Agent,  # type: ignore[type-arg]
    request: AgentInput,
    *,
    now: Callable[[], datetime] | None = None,
) -> AgentRun:
    """Run one agent to a conclusion: a finished run, or a run that raised a human task."""
    run, _ = run_agent_with_result(session, agent, request, now=now)
    return run


def run_agent_with_result(
    session: Session,
    agent: Agent,  # type: ignore[type-arg]
    request: AgentInput,
    *,
    now: Callable[[], datetime] | None = None,
) -> tuple[AgentRun, AgentResult | None]:  # type: ignore[type-arg]
    """`run_agent`, also returning what the agent produced, for callers that use it.

    The result is None when this delivery found the run already finished: at-least-once
    delivery means the first one used the result, and the caller has nothing more to do.
    """
    clock = now or (lambda: datetime.now(UTC))

    already = existing_run(session, request.idempotency_key)
    if already is not None and already.state != "running":
        # At-least-once delivery means this job may arrive twice. The second time is a no-op.
        log.info(
            "agent_run_already_done",
            agent=agent.name,
            state=already.state,
        )
        return already, None

    run = already or AgentRun(
        bid_id=request.bid_id,
        route=agent.route,
        agent=agent.name,
        idempotency_key=request.idempotency_key,
        state="running",
    )
    if already is None:
        session.add(run)
    session.flush()

    try:
        result = agent.run(request)
    except BudgetExceeded as error:
        # The meter has already paused the run and told the owner.
        run.state = "escalated"
        run.error_type = "budget_exceeded"
        run.finished_at = clock()
        task = _raise_task(
            session,
            request,
            agent,
            run,
            title=f"{agent.name} stopped: {error.scope} budget reached",
            detail=str(error),
        )
        raise Escalated(str(error), task) from error
    except (NoModelAvailable, GatewayError, ValidationError) as error:
        run.state = "escalated"
        run.error_type = type(error).__name__
        run.finished_at = clock()
        task = _raise_task(
            session,
            request,
            agent,
            run,
            title=f"{agent.name} could not complete",
            detail=str(error),
        )
        raise Escalated(str(error), task) from error
    except Exception as error:
        # Anything else (a provider SDK refusing to start without credentials, a bug) must
        # not leave the run "running" and the work waiting for ever: a person takes it.
        log.exception("agent_run_failed", agent=agent.name)
        run.state = "escalated"
        run.error_type = type(error).__name__
        run.finished_at = clock()
        task = _raise_task(
            session,
            request,
            agent,
            run,
            title=f"{agent.name} failed",
            detail=f"{type(error).__name__}: {error}"[:2000],
        )
        raise Escalated(str(error), task) from error

    uncertain = result.low_confidence(agent.confidence_threshold)
    _stamp_provenance(run, result)

    if uncertain:
        # Never fall back silently to a guess (§8.4).
        run.state = "escalated"
        run.error_type = "low_confidence"
        run.finished_at = clock()
        fields = ", ".join(a.field for a in uncertain)
        task = _raise_task(
            session,
            request,
            agent,
            run,
            title=f"{agent.name}: check {fields}",
            detail=(
                f"{agent.name} was not confident enough about {fields} "
                f"(threshold {agent.confidence_threshold}). Its reading is attached; "
                "confirm or correct it."
            ),
            payload={
                "assertions": [
                    {"field": a.field, "value": a.value, "confidence": a.confidence}
                    for a in result.assertions
                ]
            },
        )
        raise Escalated("low confidence", task)

    run.state = "succeeded"
    run.finished_at = clock()
    session.flush()
    log.info(
        "agent_run_succeeded",
        agent=agent.name,
        provider=run.provider,
        model=run.model,
    )
    return run, result


def _stamp_provenance(run: AgentRun, result: AgentResult) -> None:  # type: ignore[type-arg]
    run.provider = result.provider or run.provider
    run.model = result.model or run.model
    run.prompt_version = result.prompt_version or run.prompt_version


def _raise_task(
    session: Session,
    request: AgentInput,
    agent: Agent,  # type: ignore[type-arg]
    run: AgentRun,
    *,
    title: str,
    detail: str,
    payload: dict[str, object] | None = None,
) -> HumanTask:
    """Hand the work to a person, with enough context to finish it.

    The task carries the continuation, so completing it can queue the next job in the same
    transaction that completes it.
    """
    task = HumanTask(
        bid_id=request.bid_id,
        kind=f"agent.{agent.name}",
        title=title[:300],
        state="open",
        payload={
            "agent": agent.name,
            "route": agent.route,
            "run_id": str(run.id),
            "detail": detail,
            **(payload or {}),
        },
        continuation_task=f"agents.{agent.name}.continue",
    )
    session.add(task)
    session.flush()
    log.warning("agent_escalated", agent=agent.name, task_id=str(task.id), reason=title)
    return task


def complete_task_and_continue(
    session: Session,
    task_id: uuid.UUID,
    completed_by_id: uuid.UUID | None,
    enqueue: Callable[[Session, str, dict[str, object]], None] | None = None,
) -> HumanTask:
    """Finish a human task and queue its continuation in the same transaction.

    Same transaction deliberately: a task marked done whose continuation was never queued is
    work that has silently stopped.
    """
    task = session.get(HumanTask, task_id)
    if task is None:
        raise LookupError(f"no task {task_id}")
    if task.state == "done":
        return task

    task.state = "done"
    task.completed_by_id = completed_by_id
    task.completed_at = datetime.now(UTC)
    session.flush()

    if task.continuation_task and enqueue is not None:
        enqueue(session, task.continuation_task, {"task_id": str(task_id)})
    return task


def budget_of(request: AgentInput) -> Decimal | None:
    return Decimal(request.run_budget_sgd) if request.run_budget_sgd else None
