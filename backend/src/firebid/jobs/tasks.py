"""Queued work: the worker heartbeat, scheduled sweeps, and document parsing.

`parse.document` runs on the `parse` queue, which only the sandbox pool takes from. Every
other task runs on the ordinary worker (see `firebid.jobs.worker`).
"""

import structlog
from procrastinate import JobContext
from sqlalchemy import text

from firebid.db.engine import service_session_scope, session_scope
from firebid.db.system import record_heartbeat, record_job_result
from firebid.jobs.app import app
from firebid.jobs.worker import PARSE_QUEUE

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


@app.periodic(cron="5 * * * *", periodic_id="deadline_alerts")
@app.task(name="system.deadline_alerts", queueing_lock="system.deadline_alerts")
def deadline_alerts(timestamp: int) -> int:
    """Warn people before a tender deadline (FR-BID-03). Runs hourly; sends each alert once."""
    from firebid.notifications import get_notifier
    from firebid.services.alerts import send_deadline_alerts

    # Deadlines span bids, so this runs on the service role.
    with service_session_scope() as session:
        sent = send_deadline_alerts(session, get_notifier())
    log.info("deadline_alerts_sent", count=len(sent))
    return len(sent)


@app.periodic(cron="17 3 * * *", periodic_id="audit_partitions")
@app.task(name="system.ensure_audit_partitions", queueing_lock="system.audit_partitions")
def ensure_audit_partitions(timestamp: int, months_ahead: int = 3) -> list[str]:
    """Create audit partitions ahead of time, so an insert never lands without one."""
    created: list[str] = []
    with session_scope() as session:
        for offset in range(months_ahead + 1):
            name = session.execute(
                text(
                    "SELECT ensure_audit_event_partition("
                    "(date_trunc('month', current_date) + make_interval(months => :offset))::date)"
                ),
                {"offset": offset},
            ).scalar_one()
            created.append(str(name))
    log.info("audit_partitions_ensured", partitions=created)
    return created


@app.periodic(cron="* * * * *", periodic_id="heartbeat")
@app.task(name="system.heartbeat", queueing_lock="system.heartbeat", queue="default")
def heartbeat(timestamp: int) -> None:
    """Runs every minute. /health reports the queue unhealthy when the heartbeat is stale."""
    with session_scope() as session:
        record_heartbeat(session, HEARTBEAT_NAME)


@app.task(name="parse.document", queue=PARSE_QUEUE, pass_context=True)
def parse_document(context: JobContext, document_id: str, user_id: str) -> dict[str, int]:
    """Turn one stored document into sheets with tiles (FR-DOC-01).

    On the `parse` queue, so only the sandbox pool takes it: this job opens a file that came
    from outside the company, and the pool is the only container allowed to do that.

    Runs as `user_id`, the person who uploaded the file. Row-level security shows a
    transaction with no acting user nothing, so without it every document looks deleted.

    Idempotent, because delivery is at-least-once. Sheets are keyed by document and page and
    tiles by content hash, so running it twice re-uses everything and changes nothing.
    """
    import uuid as uuid_module

    from firebid.db.identity import acting_as
    from firebid.db.models.documents import Document
    from firebid.services.sheets import process_document
    from firebid.services.title_blocks import read_title_blocks
    from firebid.storage.object_store import get_object_store

    with acting_as(uuid_module.UUID(user_id)), session_scope() as session:
        document = session.get(Document, uuid_module.UUID(document_id))
        if document is None:
            # The bid was deleted while the job waited. Nothing to do, and not an error.
            log.info("parse_skipped_missing_document", document_id=document_id)
            return {"sheets": 0, "tiles": 0}

        store = get_object_store()
        outcome = process_document(session, store, document)
        if outcome.sheets:
            # Straight after the sheets exist, in the same sandboxed job: reading a title
            # block opens the tender file, so it cannot happen anywhere else.
            read_title_blocks(session, store, document, outcome.sheets)

    if outcome.failure:
        log.warning("parse_failed", document_id=document_id, reason=outcome.failure)
    return {"sheets": len(outcome.sheets), "tiles": outcome.tiles_written}


@app.task(name="title_block.check", queue="default", pass_context=True)
def check_title_block(context: JobContext, revision_id: str, user_id: str) -> str:
    """Ask the model about a title block the deterministic reader was unsure of (FR-DOC-02).

    On the ordinary worker, not the sandbox pool: it calls a model, which the pool cannot
    reach, and it is given a PNG the pool rendered rather than the tender file.
    """
    import uuid as uuid_module

    from firebid.ai_gateway import gateway
    from firebid.db.identity import acting_as
    from firebid.db.models.documents import SheetRevision
    from firebid.services.title_blocks import check_with_model
    from firebid.storage.object_store import get_object_store

    acting = uuid_module.UUID(user_id) if user_id else None
    with acting_as(acting), session_scope() as session:
        revision = session.get(SheetRevision, uuid_module.UUID(revision_id))
        if revision is None:
            log.info("title_block_check_skipped_missing_revision", revision_id=revision_id)
            return "missing"
        check_with_model(session, get_object_store(), revision, gateway())
        return revision.extraction_method or "unknown"
