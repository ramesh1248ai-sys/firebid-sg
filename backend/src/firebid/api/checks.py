"""Production health checks: database connectivity and job-queue heartbeat freshness."""

from datetime import UTC, datetime

from sqlalchemy import select, text

from firebid.api.health import CheckResult, HealthCheck
from firebid.db.engine import get_engine, session_scope
from firebid.db.system import SystemHeartbeat
from firebid.jobs.tasks import HEARTBEAT_NAME
from firebid.settings import Settings


def database_check() -> CheckResult:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    return CheckResult(ok=True)


def job_queue_check(max_age_seconds: int) -> HealthCheck:
    def check() -> CheckResult:
        with session_scope() as session:
            last_run = session.scalar(
                select(SystemHeartbeat.last_run_at).where(SystemHeartbeat.name == HEARTBEAT_NAME)
            )
        if last_run is None:
            return CheckResult(ok=False, detail={"last_heartbeat": None})
        age = (datetime.now(UTC) - last_run).total_seconds()
        return CheckResult(
            ok=age <= max_age_seconds,
            detail={"last_heartbeat": last_run.isoformat(), "age_seconds": round(age)},
        )

    return check


def default_checks(settings: Settings) -> dict[str, HealthCheck]:
    return {
        "database": database_check,
        "job_queue": job_queue_check(settings.heartbeat_max_age_seconds),
    }
