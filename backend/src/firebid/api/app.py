"""FastAPI application factory."""

from collections.abc import Mapping

from fastapi import FastAPI, Response
from pydantic import BaseModel

from firebid import __version__
from firebid.api.health import HealthCheck, HealthReport, run_checks
from firebid.api.middleware import RequestIdMiddleware
from firebid.logging import configure_logging
from firebid.settings import Settings, get_settings


class VersionInfo(BaseModel):
    version: str
    git_sha: str
    env: str


def create_app(
    settings: Settings | None = None,
    health_checks: Mapping[str, HealthCheck] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    checks = health_checks if health_checks is not None else {}

    app = FastAPI(title="FireBid SG API", version=__version__)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/health", response_model=HealthReport, tags=["system"])
    def health(response: Response) -> HealthReport:
        report = run_checks(checks)
        if report.status != "ok":
            response.status_code = 503
        return report

    @app.get("/version", response_model=VersionInfo, tags=["system"])
    def version() -> VersionInfo:
        return VersionInfo(version=__version__, git_sha=settings.git_sha, env=settings.env)

    return app
