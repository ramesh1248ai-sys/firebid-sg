"""Queued work: the worker heartbeat, scheduled sweeps, and document parsing.

`parse.document` runs on the `parse` queue, which only the sandbox pool takes from. Every
other task runs on the ordinary worker (see `firebid.jobs.worker`).
"""

from typing import Any

import structlog
from procrastinate import JobContext
from sqlalchemy import text

from firebid.db.engine import service_session_scope, session_scope
from firebid.db.system import record_job_result
from firebid.jobs.app import app
from firebid.jobs.worker import PARSE_QUEUE

log = structlog.get_logger(__name__)

# Kinds that become sheets. Everything the parse job reads is also classified.
SHEET_KINDS = frozenset({"pdf", "dxf"})


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


@app.task(name="system.heartbeat", queueing_lock="system.heartbeat", queue="default")
def heartbeat(timestamp: int = 0) -> None:
    """One heartbeat, as a job. No longer scheduled: the worker beats from a thread of its
    own (`firebid.jobs.heartbeat`), so a long job cannot starve it. Kept so that a heartbeat
    job still waiting from before the change runs instead of failing."""
    from firebid.jobs.heartbeat import beat

    beat()


@app.task(name="parse.document", queue=PARSE_QUEUE, pass_context=True)
def parse_document(context: JobContext, document_id: str, user_id: str) -> dict[str, int]:
    """Read one stored document: a drawing's sheets, each in its own job; a type for all.

    On the `parse` queue, so only the sandbox pool takes it: this job opens a file that came
    from outside the company, and the pool is the only container allowed to do that.

    Runs as `user_id`, the person who uploaded the file. Row-level security shows a
    transaction with no acting user nothing, so without it every document looks deleted.

    A drawing is read in stages (ADR-010): this job registers its sheets and queues a
    `parse.sheet` for each; the last of those queues `parse.finish`, which detects, classifies
    and marks the document `done`. Idempotent: sheets are keyed by document and page, and a
    parsed sheet is not queued again.
    """
    import uuid as uuid_module

    from firebid.db.identity import acting_as
    from firebid.db.models.documents import Document
    from firebid.services.classification import classify_in_sandbox
    from firebid.services.parse_pipeline import start
    from firebid.services.revisions import read_transmittal
    from firebid.storage.object_store import get_object_store

    user = uuid_module.UUID(user_id)
    with acting_as(user), session_scope() as session:
        document = session.get(Document, uuid_module.UUID(document_id))
        if document is None:
            # The bid was deleted while the job waited. Nothing to do, and not an error.
            log.info("parse_skipped_missing_document", document_id=document_id)
            return {"sheets": 0, "tiles": 0}

        store = get_object_store()
        failure: str | None = None
        sheets = 0
        if document.kind in SHEET_KINDS:
            started = start(session, store, document, user)
            failure, sheets = started.failure, started.sheets
            if failure is None and sheets:
                return {"sheets": sheets, "tiles": 0}
        if failure is None and document.state in ("received", "processing", "done"):
            payload = store.get(document.storage_key)
            if document.kind == "xlsx":
                # A drawing list or transmittal: the third source every revision is checked
                # against. Read before classifying, which then knows the workbook is one.
                read_transmittal(session, document, payload)
            classify_in_sandbox(session, document, payload)
            # A workbook, a document, or a drawing with no sheets: being read and classified
            # is what "done" means for it.
            document.state = "done"

    if failure:
        log.warning("parse_failed", document_id=document_id, reason=failure)
    return {"sheets": sheets, "tiles": 0}


@app.task(name="parse.sheet", queue=PARSE_QUEUE, pass_context=True)
def parse_sheet(context: JobContext, sheet_id: str, user_id: str) -> dict[str, Any]:
    """One sheet of a drawing, start to finish (ADR-010). In the sandbox pool, as its reader.

    A sheet whose job fails is still finished, with the reason, in a transaction of its own:
    one bad sheet never holds up the rest of its document. Skips a sheet already parsed.
    """
    import uuid as uuid_module

    from firebid.db.identity import acting_as
    from firebid.db.models.documents import Sheet
    from firebid.services import parse_pipeline
    from firebid.storage.object_store import get_object_store

    user = uuid_module.UUID(user_id)
    sheet_key = uuid_module.UUID(sheet_id)
    try:
        with acting_as(user), session_scope() as session:
            sheet = session.get(Sheet, sheet_key)
            if sheet is None:
                log.info("parse_skipped_missing_sheet", sheet_id=sheet_id)
                return {"skipped": True}
            result = parse_pipeline.parse_sheet(session, get_object_store(), sheet, user)
            return {"skipped": result.skipped, "last": result.last, "seconds": result.seconds}
    except Exception as failure:
        reason = f"{type(failure).__name__}: {failure}"
        with acting_as(user), session_scope() as session:
            last = parse_pipeline.mark_failed(session, sheet_key, user, reason)
        return {"failed": reason[:300], "last": last}


@app.task(name="parse.finish", queue=PARSE_QUEUE, pass_context=True)
def parse_finish(context: JobContext, document_id: str, user_id: str) -> dict[str, int]:
    """What needs a drawing's every sheet read: match its symbols, classify it, and queue each
    sheet to be detected (ADR-010). In the sandbox pool, because classifying opens the file.

    A finish that fails leaves the document `rejected` with the reason, never `processing`
    for good.
    """
    import uuid as uuid_module

    from firebid.db.identity import acting_as
    from firebid.db.models.documents import Document
    from firebid.services import parse_pipeline
    from firebid.storage.object_store import get_object_store

    user = uuid_module.UUID(user_id)
    try:
        with acting_as(user), session_scope() as session:
            document = session.get(Document, uuid_module.UUID(document_id))
            if document is None:
                log.info("parse_skipped_missing_document", document_id=document_id)
                return {"sheets": 0}
            return parse_pipeline.finish(session, get_object_store(), document, user)
    except Exception as failure:
        reason = f"the drawing was read but could not be finished: {type(failure).__name__}"
        log.exception("parse_finish_failed", document_id=document_id)
        with acting_as(user), session_scope() as session:
            document = session.get(Document, uuid_module.UUID(document_id))
            if document is not None:
                document.state = "rejected"
                document.rejected_reason = reason
        return {"sheets": 0}


@app.task(name="detection.sheet", queue=PARSE_QUEUE, pass_context=True)
def detect_sheet_job(
    context: JobContext, sheet_id: str, user_id: str, force: bool = False
) -> dict[str, Any]:
    """One sheet's detections: after its document's sheets are all read, or after a mapping
    decision that reaches it (ADR-010). `force` detects it whether or not anything changed.

    In the parser pool, though it opens no tender file (it reads stored geometry): that is
    where a document's sheets run side by side, and where a job whose worker stopped is
    queued again. A sheet whose detection fails is still finished, with the reason, in a
    transaction of its own: one bad sheet never holds up the rest of its document.
    """
    import uuid as uuid_module

    from firebid.db.identity import acting_as
    from firebid.db.models.documents import Sheet
    from firebid.services import parse_pipeline
    from firebid.storage.object_store import get_object_store

    user = uuid_module.UUID(user_id) if user_id else None
    sheet_key = uuid_module.UUID(sheet_id)
    try:
        with acting_as(user), session_scope() as session:
            sheet = session.get(Sheet, sheet_key)
            if sheet is None:
                log.info("detection_skipped_missing_sheet", sheet_id=sheet_id)
                return {"skipped": True}
            result = parse_pipeline.detect_sheet(
                session, get_object_store(), sheet, user, force=force
            )
            return {
                "skipped": result.skipped,
                "last": result.last,
                "unchanged": result.unchanged,
                "objects": result.objects,
                "runs": result.runs,
            }
    except Exception as failure:
        reason = f"{type(failure).__name__}: {failure}"
        with acting_as(user), session_scope() as session:
            last = parse_pipeline.mark_detection_failed(session, sheet_key, user, reason)
        return {"failed": reason[:300], "last": last}


@app.task(name="parse.complete", queue=PARSE_QUEUE, pass_context=True)
def parse_complete(context: JobContext, document_id: str, user_id: str) -> dict[str, int]:
    """A drawing whose sheets are all read and detected: queue the takeoff, mark it `done`."""
    import uuid as uuid_module

    from firebid.db.identity import acting_as
    from firebid.db.models.documents import Document
    from firebid.services import parse_pipeline

    user = uuid_module.UUID(user_id) if user_id else None
    with acting_as(user), session_scope() as session:
        document = session.get(Document, uuid_module.UUID(document_id))
        if document is None:
            log.info("parse_skipped_missing_document", document_id=document_id)
            return {"failed_detections": 0}
        return parse_pipeline.complete(session, document, user)


# How many times a job may be run before one whose worker keeps stopping is given up on.
STALLED_ATTEMPTS = 5


@app.periodic(cron="*/5 * * * *", periodic_id="retry_stalled_jobs")
@app.task(
    name="system.retry_stalled_jobs",
    aliases=["system.retry_stalled_parse"],  # its name while it looked at the parser pool only
    queue="default",
    queueing_lock="system.retry_stalled_jobs",
)
def retry_stalled_jobs(timestamp: int, heartbeat_seconds: int = 300) -> int:
    """Queue again the jobs whose worker has stopped, on any queue (ADR-006, ADR-010).

    A worker that dies mid-job leaves its job `doing` for ever: a document `processing`, a
    legend row without its proposal, a takeoff never made. Delivery is at-least-once and
    every job is idempotent, so running one again is always safe. A job that has been run
    `STALLED_ATTEMPTS` times and is found stalled again is failed instead: it is more likely
    stopping its worker than unlucky, and the failure is there to be seen.
    """
    with service_session_scope() as session:
        stalled = session.execute(
            text(
                "SELECT job.id, job.attempts, job.task_name FROM procrastinate_jobs job "
                "LEFT JOIN procrastinate_workers worker ON worker.id = job.worker_id "
                "WHERE job.status = 'doing' "
                "AND (worker.id IS NULL OR worker.last_heartbeat < "
                "now() - make_interval(secs => :seconds)) ORDER BY job.id"
            ),
            {"seconds": heartbeat_seconds},
        ).all()
        retried, abandoned = [], []
        for job_id, attempts, task_name in stalled:
            if attempts + 1 >= STALLED_ATTEMPTS:
                session.execute(
                    text("SELECT procrastinate_finish_job_v1(:id, 'failed', false)"), {"id": job_id}
                )
                abandoned.append({"id": job_id, "task": task_name})
            else:
                session.execute(
                    text("SELECT procrastinate_retry_job_v2(:id, now(), NULL, NULL, NULL)"),
                    {"id": job_id},
                )
                retried.append({"id": job_id, "task": task_name})
    # An event a job: the log keeps plain values and hides lists.
    for job in retried:
        log.warning("stalled_job_retried", job_id=job["id"], task=job["task"])
    for job in abandoned:
        log.error(
            "stalled_job_abandoned", job_id=job["id"], task=job["task"], runs=STALLED_ATTEMPTS
        )
    return len(retried)


@app.task(name="document.classify", queue="default", pass_context=True)
def classify_document_with_model(context: JobContext, document_id: str, user_id: str) -> str:
    """Ask the model which kind of document the rules could not place (FR-DOC-02).

    On the ordinary worker: it calls a model, and it is given the stored digest, never the
    file, so it has no need to be in the sandbox pool.
    """
    import uuid as uuid_module

    from firebid.ai_gateway import gateway
    from firebid.db.identity import acting_as
    from firebid.db.models.documents import Document
    from firebid.services.classification import classify_with_model

    acting = uuid_module.UUID(user_id) if user_id else None
    with acting_as(acting), session_scope() as session:
        document = session.get(Document, uuid_module.UUID(document_id))
        if document is None:
            return "missing"
        classify_with_model(session, document, gateway())
        return document.doc_type or "unknown"


@app.task(name="detection.run", queue="default", pass_context=True)
def run_detection(context: JobContext, bid_id: str, user_id: str, force: bool = False) -> int:
    """After a symbol mapping was confirmed or changed: queue the sheets it reaches to be
    detected again, each in a job of its own (`detection.sheet`).

    On the ordinary worker, and quick: it finds the sheets by fingerprint and opens no
    geometry. `force` queues every sheet regardless, for a person who asks for it. The
    takeoff follows when the sheets' documents complete (P1-07). Returns how many sheets
    it queued.
    """
    import uuid as uuid_module

    from firebid.db.identity import acting_as
    from firebid.services import parse_pipeline

    acting = uuid_module.UUID(user_id) if user_id else None
    with acting_as(acting), session_scope() as session:
        queued = parse_pipeline.detect_again(session, uuid_module.UUID(bid_id), acting, force=force)
        return queued["sheets"]


@app.task(name="qto.recompute", queue="default", pass_context=True)
def recompute_qto(context: JobContext, bid_id: str, user_id: str) -> dict[str, int]:
    """Take off a bid again from its Current sheets' detections (P1-07).

    Idempotent: the same inputs leave every item, and its verification, as it was.
    """
    import uuid as uuid_module

    from firebid.db.identity import acting_as
    from firebid.services.qto import recompute

    acting = uuid_module.UUID(user_id) if user_id else None
    with acting_as(acting), session_scope() as session:
        outcome = recompute(session, uuid_module.UUID(bid_id))
        return {
            "created": outcome.created,
            "unchanged": outcome.unchanged,
            "superseded": outcome.superseded,
            "incomplete": len(outcome.incomplete),
        }


@app.task(name="detection.vision", queue="default", pass_context=True)
def classify_with_vision_job(
    context: JobContext,
    sheet_id: str,
    entry_id: str,
    box: list[float],
    rows: list[int],
    crop_key: str,
    distance: float,
    user_id: str,
) -> str:
    """Vision assist: ask the model about a symbol nearly like a legend entry (P1-05).

    On the ordinary worker: it calls a model, with a crop drawn from the geometry.
    """
    import uuid as uuid_module

    from firebid.ai_gateway import gateway
    from firebid.db.identity import acting_as
    from firebid.services.detection import classify_with_vision
    from firebid.storage.object_store import get_object_store

    acting = uuid_module.UUID(user_id) if user_id else None
    with acting_as(acting), session_scope() as session:
        found = classify_with_vision(
            session,
            get_object_store(),
            gateway(),
            sheet_id=uuid_module.UUID(sheet_id),
            entry_id=uuid_module.UUID(entry_id),
            box=box,
            rows=rows,
            crop_key=crop_key,
            distance=distance,
        )
        return found.object_type if found else "none"


@app.task(name="spec.read", queue="parse", pass_context=True)
def read_specification_job(context: JobContext, document_id: str, user_id: str) -> int:
    """A specification's clause tree and rule attributes (FR-SPEC-01, 05).

    In the sandbox pool: it opens the tender file. Model work it cannot do is queued on.
    """
    import uuid as uuid_module

    from firebid.db.identity import acting_as
    from firebid.db.models.documents import Document
    from firebid.services.specs import read_specification
    from firebid.storage.object_store import get_object_store

    acting = uuid_module.UUID(user_id) if user_id else None
    with acting_as(acting), session_scope() as session:
        document = session.get(Document, uuid_module.UUID(document_id))
        if document is None:
            return 0
        revision = read_specification(session, get_object_store(), document, user_id=user_id)
        return 1 if revision is not None else 0


@app.task(name="spec.sections", queue="default", pass_context=True)
def find_spec_sections_job(context: JobContext, revision_id: str, user_id: str) -> int:
    """The model places specification sections the heading rules could not."""
    import uuid as uuid_module

    from firebid.ai_gateway import gateway
    from firebid.db.identity import acting_as
    from firebid.services.specs import find_sections_with_model

    acting = uuid_module.UUID(user_id) if user_id else None
    with acting_as(acting), session_scope() as session:
        return find_sections_with_model(
            session, uuid_module.UUID(revision_id), gateway(), user_id=user_id
        )


@app.task(name="spec.attributes", queue="default", pass_context=True)
def read_spec_attributes_job(
    context: JobContext, revision_id: str, system: str, clause_numbers: list[str], user_id: str
) -> int:
    """The model reads fire protection clauses the rules read nothing from."""
    import uuid as uuid_module

    from firebid.ai_gateway import gateway
    from firebid.db.identity import acting_as
    from firebid.services.specs import read_attributes_with_model

    acting = uuid_module.UUID(user_id) if user_id else None
    with acting_as(acting), session_scope() as session:
        return len(
            read_attributes_with_model(
                session, uuid_module.UUID(revision_id), system, clause_numbers, gateway()
            )
        )


@app.task(name="symbol.propose", queue="default", pass_context=True)
def propose_symbol(context: JobContext, entry_id: str, user_id: str) -> str:
    """Ask the model what a legend row's symbol is, when the keyword rules could not say.

    On the ordinary worker, not the sandbox pool: it calls a model, and it is given a PNG of
    the row drawn from the extracted geometry, not the tender file. Its answer is a proposal
    that a person confirms (FR-VIS-02).
    """
    import uuid as uuid_module

    from firebid.ai_gateway import gateway
    from firebid.db.identity import acting_as
    from firebid.db.models.symbols import LegendEntry
    from firebid.services.symbols import propose_with_model
    from firebid.storage.object_store import get_object_store

    acting = uuid_module.UUID(user_id) if user_id else None
    with acting_as(acting), session_scope() as session:
        entry = session.get(LegendEntry, uuid_module.UUID(entry_id))
        if entry is None:
            log.info("symbol_propose_skipped_missing_entry", entry_id=entry_id)
            return "missing"
        return propose_with_model(session, get_object_store(), entry, gateway()).status


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


@app.task(name="boq.read", queue="parse", pass_context=True)
def read_client_boq_job(context: JobContext, document_id: str, user_id: str) -> int:
    """A client's BOQ workbook read in the sandbox pool (FR-BOQ-02). Its bytes are never
    written; an ambiguous layout waits for a person."""
    import uuid as uuid_module

    from firebid.db.identity import acting_as
    from firebid.db.models.documents import Document
    from firebid.services.boq import read_client_boq
    from firebid.storage.object_store import get_object_store

    acting = uuid_module.UUID(user_id) if user_id else None
    with acting_as(acting), session_scope() as session:
        document = session.get(Document, uuid_module.UUID(document_id))
        if document is None:
            return 0
        return len(read_client_boq(session, get_object_store(), document, user_id=user_id))


@app.task(name="boq.columns", queue="default", pass_context=True)
def propose_boq_columns_job(context: JobContext, client_boq_id: str, user_id: str) -> int:
    """The model proposes a bill's header row and columns; a person confirms them."""
    import uuid as uuid_module

    from firebid.ai_gateway import gateway
    from firebid.db.identity import acting_as
    from firebid.db.models.commercial import ClientBoq
    from firebid.services.boq import propose_columns_with_model

    acting = uuid_module.UUID(user_id) if user_id else None
    with acting_as(acting), session_scope() as session:
        row = session.get(ClientBoq, uuid_module.UUID(client_boq_id))
        if row is None:
            return 0
        propose_columns_with_model(session, row, gateway())
        return 1


@app.task(name="boq.map", queue="default", pass_context=True)
def propose_boq_mappings_job(context: JobContext, bid_id: str, user_id: str) -> int:
    """Rules then the model propose which measured line each client line is."""
    import uuid as uuid_module

    from firebid.ai_gateway import gateway
    from firebid.db.identity import acting_as
    from firebid.services.boq import propose_mappings

    acting = uuid_module.UUID(user_id) if user_id else None
    with acting_as(acting), session_scope() as session:
        return propose_mappings(session, uuid_module.UUID(bid_id), gateway())


@app.task(name="pricing.match", queue="default", pass_context=True)
def match_rates_job(context: JobContext, bid_id: str, user_id: str) -> int:
    """The model proposes rate library entries for lines with only partial matches."""
    import uuid as uuid_module

    from firebid.ai_gateway import gateway
    from firebid.db.identity import acting_as
    from firebid.db.models.core import Bid
    from firebid.domain.actors import SYSTEM_ACTOR
    from firebid.services.pricing import price_boq

    acting = uuid_module.UUID(user_id) if user_id else None
    with acting_as(acting), session_scope() as session:
        bid = session.get(Bid, uuid_module.UUID(bid_id))
        if bid is None:
            return 0
        return price_boq(session, bid, SYSTEM_ACTOR, gateway()).proposed


@app.periodic(cron="40 2 * * *", periodic_id="retention")
@app.task(name="system.retention", queueing_lock="system.retention")
def retention(timestamp: int) -> dict[str, int]:
    """Delete what the retention policy no longer keeps; archive old audit months (NFR-07/09)."""
    from firebid.db.engine import service_session_scope
    from firebid.services import retention as policy
    from firebid.storage.object_store import get_object_store

    with service_session_scope() as session:
        run = policy.purge(session)
        archived = policy.archive_months(session, get_object_store())
    return {**run.deleted, "archived_months": len(archived)}


@app.periodic(cron="50 3 * * *", periodic_id="verify_audit_chains")
@app.task(name="system.verify_audit_chains", queueing_lock="system.verify_audit_chains")
def verify_audit_chains(timestamp: int) -> int:
    """Every audit chain verified end to end each night. A break is logged for the alert."""
    from firebid.db.engine import service_session_scope
    from firebid.services.retention import verify_all_chains

    with service_session_scope() as session:
        return len(verify_all_chains(session))


@app.task(name="design.basis", queue="default", pass_context=True)
def read_design_basis(context: JobContext, bid_id: str, user_id: str) -> int:
    """Read each Current plan sheet's design basis from its stored geometry (P1-12).

    On the ordinary worker: it reads stored geometry, never the tender file. Idempotent: a
    confirmed sheet keeps its confirmation.
    """
    import uuid as uuid_module

    from firebid.db.identity import acting_as
    from firebid.services.design import read_basis
    from firebid.storage.object_store import get_object_store

    acting = uuid_module.UUID(user_id) if user_id else None
    with acting_as(acting), session_scope() as session:
        return len(read_basis(session, get_object_store(), uuid_module.UUID(bid_id)))


@app.task(name="design.layout", queue="default", pass_context=True)
def lay_out_design(
    context: JobContext, bid_id: str, user_id: str, sheet_id: str = ""
) -> dict[str, int]:
    """Lay out the sheet whose design basis a person confirmed, then take off again (P1-12).

    Without a sheet, every confirmed sheet of the bid. Idempotent: the same sheet, criterion
    and rules give the same proposal.
    """
    import uuid as uuid_module

    from firebid.db.identity import acting_as
    from firebid.services.design import lay_out_bid
    from firebid.services.qto import queue_recompute
    from firebid.storage.object_store import get_object_store

    acting = uuid_module.UUID(user_id) if user_id else None
    with acting_as(acting), session_scope() as session:
        only = uuid_module.UUID(sheet_id) if sheet_id else None
        outcome = lay_out_bid(session, get_object_store(), uuid_module.UUID(bid_id), only)
        # Takeoff follows what was proposed, in the same transaction.
        queue_recompute(session, uuid_module.UUID(bid_id), acting)
        return {"sheets": outcome.sheets, "heads": outcome.heads, "blocked": outcome.blocked}
