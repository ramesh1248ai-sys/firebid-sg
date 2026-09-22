"""Queue jobs inside the caller's database transaction.

The job row is written on the same connection as the caller's data, so a rollback removes
the job too and a commit makes both visible together (project-context convention "Jobs").
Delivery is at-least-once, so every task must be idempotent.
"""

from typing import Any

from procrastinate.tasks import Task
from sqlalchemy.orm import Session


def enqueue(session: Session, task: Task[..., Any, ...], /, **task_kwargs: Any) -> int:
    """Queue ``task`` in ``session``'s transaction and return the job ID."""
    driver_connection = session.connection().connection.driver_connection
    job_id: int = task.configure(connection=driver_connection).defer(**task_kwargs)
    return job_id
