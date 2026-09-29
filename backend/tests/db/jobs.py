"""Running a document's parse jobs to the end, as the sandbox pool would (ADR-010).

`parse.document` only registers a drawing's sheets and queues a `parse.sheet` for each; the
last sheet queues `parse.finish`. A test that wants the drawing read runs them all, in the
order they were queued, taking each job off the queue as a worker would.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from procrastinate import JobContext
from sqlalchemy import text
from sqlalchemy.orm import Session

from firebid.jobs.tasks import parse_document, parse_finish, parse_sheet

_NEXT = text(
    """
    SELECT job.id, job.task_name, job.args FROM procrastinate_jobs job
    WHERE job.status = 'todo' AND (
        (job.task_name = 'parse.sheet' AND (job.args->>'sheet_id')::uuid IN (
            SELECT id FROM sheet WHERE document_id = :document))
        OR (job.task_name = 'parse.finish' AND job.args->>'document_id' = :document_text)
    )
    ORDER BY job.id LIMIT 1
    """
)


def run_parse(session: Session, document_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, Any]:
    """`parse.document`, then every sheet and finish job it leads to. Returns what
    `parse.document` returned, plus what each later job did."""
    context = cast(JobContext, None)
    result: dict[str, Any] = dict(
        parse_document(context, document_id=str(document_id), user_id=str(user_id))
    )
    ran: list[dict[str, Any]] = []
    while True:
        session.commit()  # a fresh snapshot: the jobs just committed their work and queue
        row = session.execute(
            _NEXT, {"document": document_id, "document_text": str(document_id)}
        ).first()
        if row is None:
            break
        job_id, task_name, args = row
        session.execute(text("DELETE FROM procrastinate_jobs WHERE id = :id"), {"id": job_id})
        session.commit()
        task = parse_sheet if task_name == "parse.sheet" else parse_finish
        ran.append({"task": task_name, "result": task(context, **args)})
    session.expire_all()
    result["jobs"] = ran
    return result
