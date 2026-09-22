"""System tasks: an example job and the worker heartbeat."""

import structlog
from procrastinate import JobContext

from firebid.db.engine import session_scope
from firebid.db.system import record_heartbeat, record_job_result
from firebid.jobs.app import app

log = structlog.get_logger(__name__)

HEARTBEAT_NAME = "worker"


@app.task(name="system.add_example", pass_context=True, queue="default")
def add_example(context: JobContext, a: int, b: int) -> int:
    """Example job: adds two numbers and records the result. Idempotent on job ID."""
    job_id = context.job.id
    if job_id is None:
        raise RuntimeError("job has no ID; it must be queued before it runs")
    total = a + b
    with session_scope() as session:
        record_job_result(session, job_id, "system.add_example", {"sum": total})
    log.info("example_job_done", job_id=job_id)
    return total


@app.periodic(cron="* * * * *", periodic_id="heartbeat")
@app.task(name="system.heartbeat", queueing_lock="system.heartbeat", queue="default")
def heartbeat(timestamp: int) -> None:
    """Runs every minute. /health reports the queue unhealthy when the heartbeat is stale."""
    with session_scope() as session:
        record_heartbeat(session, HEARTBEAT_NAME)
