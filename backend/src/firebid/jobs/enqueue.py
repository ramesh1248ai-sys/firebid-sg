"""Queue jobs inside the caller's database transaction.

The job row is written on the same connection as the caller's data, so a rollback removes
the job too and a commit makes both visible together (project-context convention "Jobs").
Delivery is at-least-once, so every task must be idempotent.

Always through a synchronous job manager, never the app's own connector. Inside the worker
that connector is open in async mode, and given the caller's synchronous connection it fails
("'Cursor' object does not support the asynchronous context manager protocol"). A job that
queues another, as parsing queues the title block check, runs exactly there.
"""

from functools import lru_cache
from typing import Any

from procrastinate import SyncPsycopgConnector
from procrastinate.jobs import JobDeferrer
from procrastinate.manager import JobManager
from procrastinate.tasks import Task
from sqlalchemy.orm import Session


@lru_cache
def _manager() -> JobManager:
    # Never opened: it only ever runs queries on a connection it is handed.
    return JobManager(SyncPsycopgConnector())


def enqueue(session: Session, task: Task[..., Any, ...], /, **task_kwargs: Any) -> int:
    """Queue ``task`` in ``session``'s transaction and return the job ID."""
    connection = session.connection().connection.driver_connection
    job = task.configure(connection=connection).job
    job_id: int = JobDeferrer(_manager(), job, connection=connection).defer(**task_kwargs)
    return job_id
