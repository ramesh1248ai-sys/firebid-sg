import json
import logging

import pytest
from fastapi.testclient import TestClient

from firebid import __version__
from firebid.api.app import create_app
from firebid.api.health import CheckResult, HealthCheck
from firebid.settings import Settings


def fixed(result: CheckResult) -> HealthCheck:
    return lambda: result


def make_client(checks: dict[str, CheckResult] | None = None) -> TestClient:
    health_checks = {name: fixed(result) for name, result in (checks or {}).items()}
    return TestClient(create_app(Settings(env="test"), health_checks=health_checks))


def test_health_ok_when_all_checks_pass() -> None:
    client = make_client({"database": CheckResult(ok=True)})
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_503_when_a_check_fails() -> None:
    client = make_client({"database": CheckResult(ok=True), "job_queue": CheckResult(ok=False)})
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["checks"]["job_queue"]["ok"] is False


def test_health_reports_exception_type_not_message() -> None:
    def broken() -> CheckResult:
        raise RuntimeError("password=secret")

    client = TestClient(create_app(Settings(env="test"), health_checks={"database": broken}))
    body = client.get("/health").json()
    assert body["checks"]["database"] == {"ok": False, "detail": {"error": "RuntimeError"}}
    assert "secret" not in json.dumps(body)


def test_version() -> None:
    body = make_client().get("/version").json()
    assert body["version"] == __version__
    assert body["env"] == "test"


@pytest.mark.req("NFR-14")
def test_every_response_carries_a_request_id_that_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = make_client()
    with caplog.at_level(logging.INFO, logger="firebid.request"):
        response = client.get("/version")
    request_id = response.headers["x-request-id"]
    assert len(request_id) == 32
    assert any(request_id in record.getMessage() for record in caplog.records)


@pytest.mark.req("NFR-14")
def test_valid_inbound_request_id_is_kept_and_invalid_one_replaced() -> None:
    client = make_client()
    kept = client.get("/version", headers={"x-request-id": "trace-abc-12345"})
    assert kept.headers["x-request-id"] == "trace-abc-12345"
    replaced = client.get("/version", headers={"x-request-id": "<script>"})
    assert replaced.headers["x-request-id"] != "<script>"
