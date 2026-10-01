"""The worker's heartbeat: beside its jobs, not among them.

`/health` reports the job queue unhealthy when the heartbeat is stale. It used to be a job on
the queue, and the worker runs one job at a time (ADR-006), so a job longer than the allowed
age starved it: a four-minute detection on a real tender turned a working system red, and
ten of them in a row kept it red for forty minutes.

It is now a thread of the worker process. It says the worker is alive and can reach the
database, whatever job the worker is running. Whether jobs are *moving* is a different
question, answered by the queue depths it logs each beat (`queue_depth`), which the
monitoring alert on a backed-up queue reads (ADR-008).
"""

from __future__ import annotations

import threading

import structlog
from sqlalchemy import text

from firebid.db.engine import session_scope
from firebid.db.system import record_heartbeat

log = structlog.get_logger(__name__)

HEARTBEAT_NAME = "worker"
SECONDS = 30.0
QUEUES = ("default", "system", "parse")


def beat() -> dict[str, int]:
    """Record one heartbeat and log how many jobs wait in each queue."""
    with session_scope() as session:
        record_heartbeat(session, HEARTBEAT_NAME)
        waiting = session.execute(
            text(
                "SELECT queue_name, count(*) FROM procrastinate_jobs "
                "WHERE status = 'todo' GROUP BY queue_name"
            )
        ).tuples()
        depths = {str(queue): int(count) for queue, count in waiting.all()}
    for queue in sorted(set(QUEUES) | set(depths)):
        log.info("queue_depth", queue=queue, todo=depths.get(queue, 0))
    return depths


def start(seconds: float = SECONDS) -> threading.Event:
    """Beat now and every `seconds` after, until the returned event is set.

    A daemon thread: it never holds the worker open. A beat that fails (the database is
    away) is logged and the next one tried: that is exactly what a stale heartbeat reports.
    """
    stop = threading.Event()

    def run() -> None:
        while True:
            try:
                beat()
            except Exception as failure:
                log.warning("heartbeat_failed", reason=str(failure))
            if stop.wait(seconds):
                return

    threading.Thread(target=run, name="firebid-heartbeat", daemon=True).start()
    return stop
