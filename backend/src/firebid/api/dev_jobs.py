"""Development-only job endpoints: queue the example job and read job history.

Mounted only when FIREBID_ENV is dev or test. Real, authorised job views arrive with P0-03.
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from firebid.db.engine import session_scope
from firebid.jobs import enqueue
from firebid.jobs.tasks import add_example

router = APIRouter(prefix="/dev/jobs", tags=["dev"])


class ExampleJobRequest(BaseModel):
    a: int
    b: int


class JobQueued(BaseModel):
    job_id: int


class JobRecord(BaseModel):
    job_id: int
    task_name: str
    status: str
    attempts: int
    result: dict[str, Any] | None
    finished_at: datetime | None


_HISTORY_SQL = """
    SELECT j.id AS job_id, j.task_name, j.status::text AS status, j.attempts,
           r.result, r.finished_at
    FROM procrastinate_jobs j
    LEFT JOIN system_job_result r ON r.job_id = j.id
"""


@router.post("/example", response_model=JobQueued, status_code=202)
def queue_example_job(body: ExampleJobRequest) -> JobQueued:
    with session_scope() as session:
        job_id = enqueue(session, add_example, a=body.a, b=body.b)
    return JobQueued(job_id=job_id)


@router.get("", response_model=list[JobRecord])
def job_history(limit: int = 20) -> list[JobRecord]:
    with session_scope() as session:
        rows = session.execute(
            text(_HISTORY_SQL + " ORDER BY j.id DESC LIMIT :limit"), {"limit": min(limit, 100)}
        ).mappings()
        return [JobRecord.model_validate(dict(row)) for row in rows]


@router.get("/{job_id}", response_model=JobRecord)
def job_detail(job_id: int) -> JobRecord:
    with session_scope() as session:
        row = (
            session.execute(text(_HISTORY_SQL + " WHERE j.id = :job_id"), {"job_id": job_id})
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    return JobRecord.model_validate(dict(row))
