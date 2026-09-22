"""Health checks. Each check reports independently; any failing check makes /health return 503."""

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel


class CheckResult(BaseModel):
    ok: bool
    detail: dict[str, Any] = {}


class HealthReport(BaseModel):
    status: str
    checks: dict[str, CheckResult]


HealthCheck = Callable[[], CheckResult]


def run_checks(checks: Mapping[str, HealthCheck]) -> HealthReport:
    results: dict[str, CheckResult] = {}
    for name, check in checks.items():
        try:
            results[name] = check()
        except Exception as exc:
            results[name] = CheckResult(ok=False, detail={"error": type(exc).__name__})
    status = "ok" if all(r.ok for r in results.values()) else "unhealthy"
    return HealthReport(status=status, checks=results)
