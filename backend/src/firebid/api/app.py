"""FastAPI application factory."""

from collections.abc import Mapping

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from firebid import __version__
from firebid.api.health import HealthCheck, HealthReport, run_checks
from firebid.api.middleware import RequestIdMiddleware
from firebid.api.security import (
    BodySizeLimitMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from firebid.logging import configure_logging
from firebid.settings import Settings, get_settings


class VersionInfo(BaseModel):
    version: str
    git_sha: str
    env: str


class Liveness(BaseModel):
    status: str


def create_app(
    settings: Settings | None = None,
    health_checks: Mapping[str, HealthCheck] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    if health_checks is None:
        from firebid.api.checks import default_checks

        health_checks = default_checks(settings)
    checks = health_checks

    app = FastAPI(title="FireBid SG API", version=__version__)
    # Starlette runs middleware outermost-last, so the request ID is bound before anything
    # else can log or refuse, and the security headers reach even a refusal.
    app.add_middleware(
        RateLimitMiddleware,
        requests_per_minute=settings.rate_limit_per_minute,
        exempt_paths=("/health", "/health/live"),
    )
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=settings.max_body_bytes,
        upload_max_bytes=settings.max_upload_bytes,
    )
    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_allowed_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["authorization", "content-type", "x-request-id"],
            max_age=600,
        )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/health", response_model=HealthReport, tags=["system"])
    def health(response: Response) -> HealthReport:
        """Readiness: every dependency, including a fresh job-queue heartbeat."""
        report = run_checks(checks)
        if report.status != "ok":
            response.status_code = 503
        return report

    @app.get("/health/live", response_model=Liveness, tags=["system"])
    def live() -> Liveness:
        """Liveness: the process is serving requests. Used by container health checks."""
        return Liveness(status="ok")

    @app.get("/version", response_model=VersionInfo, tags=["system"])
    def version() -> VersionInfo:
        return VersionInfo(version=__version__, git_sha=settings.git_sha, env=settings.env)

    from firebid.api import (
        addenda,
        admin,
        audit,
        bids,
        detections,
        documents,
        library,
        progress,
        registers,
        sheets,
        symbols,
        views,
    )

    app.include_router(bids.router)
    app.include_router(documents.router)
    app.include_router(sheets.router)
    app.include_router(views.router)
    app.include_router(symbols.router)
    app.include_router(detections.router)
    app.include_router(library.router)
    app.include_router(registers.router)
    app.include_router(addenda.router)
    app.include_router(progress.router)
    app.include_router(audit.router)
    app.include_router(admin.router)

    if settings.env in ("dev", "test"):
        from firebid.api import dev_jobs

        app.include_router(dev_jobs.router)

    return app


def app_factory() -> FastAPI:
    """Entry point for ``uvicorn --factory firebid.api.app:app_factory``."""
    return create_app()
