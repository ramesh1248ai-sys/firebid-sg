"""System tables: worker heartbeat and job results."""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from firebid.db.base import Base


class SystemHeartbeat(Base):
    __tablename__ = "system_heartbeat"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SystemJobResult(Base):
    """Return values of jobs that report a result, keyed by the queue's job ID."""

    __tablename__ = "system_job_result"

    job_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    task_name: Mapped[str] = mapped_column(String(128))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


def record_heartbeat(session: Session, name: str) -> None:
    stmt = insert(SystemHeartbeat).values(name=name, last_run_at=func.now())
    session.execute(
        stmt.on_conflict_do_update(index_elements=["name"], set_={"last_run_at": func.now()})
    )


def record_job_result(
    session: Session, job_id: int, task_name: str, result: dict[str, Any]
) -> None:
    """Idempotent: a retried job (at-least-once delivery) keeps its first recorded result."""
    stmt = insert(SystemJobResult).values(job_id=job_id, task_name=task_name, result=result)
    session.execute(stmt.on_conflict_do_nothing(index_elements=["job_id"]))
