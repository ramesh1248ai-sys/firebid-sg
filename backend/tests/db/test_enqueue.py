"""Queueing a job in the caller's transaction, from the API and from inside a running job.

A parse job queues the title block check; a classification queues the model. Those run
inside the worker, where Procrastinate's connector is open in async mode, and queueing on the
caller's synchronous connection must still work there.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from firebid.jobs.app import app
from firebid.jobs.enqueue import enqueue
from firebid.jobs.tasks import add_example


def queued(session: Session, job_id: int) -> dict[str, Any] | None:
    row = session.execute(
        text("SELECT task_name, args FROM procrastinate_jobs WHERE id = :id"), {"id": job_id}
    ).first()
    return None if row is None else {"task": row.task_name, "args": row.args}


def test_a_job_is_queued_in_the_callers_transaction(session: Session) -> None:
    job_id = enqueue(session, add_example, a=1, b=2)

    assert queued(session, job_id) == {"task": "system.add_example", "args": {"a": 1, "b": 2}}
    session.rollback()
    assert queued(session, job_id) is None, "a rollback takes the job with it"


def test_a_job_can_be_queued_from_inside_the_worker(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In the worker the connector has an async pool open. Queueing must not go through it."""
    monkeypatch.setattr(app.connector, "_async_pool", object(), raising=False)

    job_id = enqueue(session, add_example, a=3, b=4)

    assert queued(session, job_id) == {"task": "system.add_example", "args": {"a": 3, "b": 4}}
