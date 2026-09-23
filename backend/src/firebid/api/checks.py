"""Production health checks: the database, the job-queue heartbeat, the scanner, the LLM routing."""

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


def llm_routing_check() -> CheckResult:
    """The routing table loads and every route still resolves to an approved model.

    A configuration edit is the supported way to change providers, so a bad edit has to be
    visible here rather than at the moment an estimator asks an agent for something.
    """
    from firebid.ai_gateway.config import load_config
    from firebid.ai_gateway.errors import ConfigError

    try:
        config = load_config()
    except ConfigError as error:
        return CheckResult(ok=False, detail={"error": str(error).splitlines()[0]})

    unserviceable = [name for name in config.routes if not config.enabled_chain(name)]
    return CheckResult(
        ok=not unserviceable,
        detail={
            "config_version": config.config_hash,
            "routes": len(config.routes),
            "providers": sorted(name for name, p in config.providers.items() if p.enabled),
            **({"routes_without_an_enabled_model": unserviceable} if unserviceable else {}),
        },
    )


def malware_scanner_check() -> CheckResult:
    """clamd answers, and says which signature set it has.

    Worth its own check because a scanner outage does not stop uploads — it holds them — so
    without this the first sign of trouble is an estimator asking why nothing has been read.
    """
    from firebid.ingest.scanning import ClamAvScanner, get_scanner

    scanner = get_scanner()
    if not isinstance(scanner, ClamAvScanner):
        return CheckResult(ok=True, detail={"scanner": type(scanner).__name__})
    return CheckResult(ok=scanner.available(), detail={"scanner": "clamd"})


def default_checks(settings: Settings) -> dict[str, HealthCheck]:
    return {
        "database": database_check,
        "job_queue": job_queue_check(settings.heartbeat_max_age_seconds),
        "malware_scanner": malware_scanner_check,
        "llm_routing": llm_routing_check,
    }
