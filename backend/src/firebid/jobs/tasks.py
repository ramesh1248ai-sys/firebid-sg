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


@app.periodic(cron="* * * * *", periodic_id="heartbeat")
@app.task(name="system.heartbeat", queueing_lock="system.heartbeat", queue="default")
def heartbeat(timestamp: int) -> None:
    """Runs every minute. /health reports the queue unhealthy when the heartbeat is stale."""
    with session_scope() as session:
        record_heartbeat(session, HEARTBEAT_NAME)


@app.task(name="parse.document", queue=PARSE_QUEUE, pass_context=True)
def parse_document(context: JobContext, document_id: str, user_id: str) -> dict[str, int]:
    """Read one stored document: sheets and title blocks for a drawing, and a type for all.

    On the `parse` queue, so only the sandbox pool takes it: this job opens a file that came
    from outside the company, and the pool is the only container allowed to do that.

    Runs as `user_id`, the person who uploaded the file. Row-level security shows a
    transaction with no acting user nothing, so without it every document looks deleted.

    Idempotent, because delivery is at-least-once. Sheets are keyed by document and page,
    tiles by content hash, and title block proposals by sheet, so running it twice re-uses
    everything and changes nothing.
    """
    import uuid as uuid_module

    from firebid.db.identity import acting_as
    from firebid.db.models.documents import Document
    from firebid.services.classification import classify_in_sandbox
    from firebid.services.detection import detect_all as detect_sheets
    from firebid.services.geometry import extract_all
    from firebid.services.revisions import read_transmittal
    from firebid.services.sheets import process_document
    from firebid.services.symbols import read_all as read_symbols
    from firebid.services.title_blocks import read_title_blocks
    from firebid.services.views import detect_all
    from firebid.storage.object_store import get_object_store

    with acting_as(uuid_module.UUID(user_id)), session_scope() as session:
        document = session.get(Document, uuid_module.UUID(document_id))
        if document is None:
            # The bid was deleted while the job waited. Nothing to do, and not an error.
            log.info("parse_skipped_missing_document", document_id=document_id)
            return {"sheets": 0, "tiles": 0}

        store = get_object_store()
        failure: str | None = None
        sheets = 0
        tiles = 0
        if document.kind in SHEET_KINDS:
            outcome = process_document(session, store, document)
            failure = outcome.failure
            sheets, tiles = len(outcome.sheets), outcome.tiles_written
            if outcome.sheets:
                # Straight after the sheets exist, in the same sandboxed job: reading a title
                # block opens the tender file, so it cannot happen anywhere else.
                read_title_blocks(session, store, document, outcome.sheets)
                # Geometry for symbol matching, pipe tracing and measurement (FR-VIS-01).
                extract_all(session, store, document, outcome.sheets)
                # Views, their scales and grids, from that geometry (FR-VIS-05/07/08).
                detect_all(session, store, outcome.sheets)
                # Legends, symbol mappings and instances, from the same geometry (FR-VIS-02).
                read_symbols(session, store, outcome.sheets, user_id=user_id)
                # Detections from the symbols already confirmed for this consultant (FR-VIS-03).
                detect_sheets(session, store, outcome.sheets)
                # And takeoff from them, on the ordinary worker (P1-07).
                from firebid.services.qto import queue_recompute

                queue_recompute(session, document.bid_id, uuid_module.UUID(user_id))
        if failure is None and document.state in ("received", "done"):
            payload = store.get(document.storage_key)
            if document.kind == "xlsx":
                # A drawing list or transmittal: the third source every revision is checked
                # against. Read before classifying, which then knows the workbook is one.
                read_transmittal(session, document, payload)
            classify_in_sandbox(session, document, payload)
            # A workbook or a document becomes no sheets; being read and classified is
            # what "done" means for it.
            document.state = "done"

    if failure:
        log.warning("parse_failed", document_id=document_id, reason=failure)
    return {"sheets": sheets, "tiles": tiles}


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
def run_detection(context: JobContext, bid_id: str, user_id: str) -> int:
    """Detect every sheet of a bid again, after a symbol mapping was confirmed or changed.

    On the ordinary worker: it reads stored geometry, never the tender file.
    """
    import uuid as uuid_module

    from firebid.db.identity import acting_as
    from firebid.services.detection import detect_bid
    from firebid.services.qto import queue_recompute
    from firebid.storage.object_store import get_object_store

    acting = uuid_module.UUID(user_id) if user_id else None
    with acting_as(acting), session_scope() as session:
        outcomes = detect_bid(session, get_object_store(), uuid_module.UUID(bid_id))
        # Takeoff follows what was detected (P1-07), in the same transaction.
        queue_recompute(session, uuid_module.UUID(bid_id), acting)
        return sum(outcome.objects + outcome.runs for outcome in outcomes)


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
