"""Job queue behaviour against a real PostgreSQL (make up, then make test-integration)."""

import os
import time

import httpx2 as httpx
import pytest
from sqlalchemy import text

from firebid.db.engine import _session_factory
from firebid.jobs import enqueue
from firebid.jobs.tasks import add_example

pytestmark = pytest.mark.integration

API_URL = os.environ.get("FIREBID_API_URL", "http://localhost:8000")


def job_exists(job_id: int) -> bool:
    with _session_factory()() as session:
        found = session.scalar(
            text("SELECT count(*) FROM procrastinate_jobs WHERE id = :id"), {"id": job_id}
        )
    return bool(found)


def test_job_queued_in_a_rolled_back_transaction_never_exists() -> None:
    session = _session_factory()()
    try:
        job_id = enqueue(session, add_example, a=1, b=2)
        assert job_exists(job_id) is False  # not visible outside the open transaction
        session.rollback()
    finally:
        session.close()
    assert job_exists(job_id) is False


def test_job_queued_in_a_committed_transaction_is_visible() -> None:
    session = _session_factory()()
    try:
        job_id = enqueue(session, add_example, a=3, b=4)
        session.commit()
    finally:
        session.close()
    assert job_exists(job_id) is True


def test_worker_runs_the_example_job_queued_through_the_api() -> None:
    response = httpx.post(f"{API_URL}/dev/jobs/example", json={"a": 20, "b": 22}, timeout=10)
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    deadline = time.monotonic() + 60
    record = {}
    while time.monotonic() < deadline:
        record = httpx.get(f"{API_URL}/dev/jobs/{job_id}", timeout=10).json()
        if record["status"] in ("succeeded", "failed"):
            break
        time.sleep(1)
    assert record["status"] == "succeeded"
    assert record["result"] == {"sum": 42}

    history = httpx.get(f"{API_URL}/dev/jobs", timeout=10).json()
    assert any(item["job_id"] == job_id for item in history)


def test_health_reports_a_fresh_heartbeat() -> None:
    deadline = time.monotonic() + 90  # the heartbeat runs every minute
    body = {}
    while time.monotonic() < deadline:
        response = httpx.get(f"{API_URL}/health", timeout=10)
        body = response.json()
        if response.status_code == 200:
            break
        time.sleep(3)
    assert body["status"] == "ok"
    assert body["checks"]["job_queue"]["detail"]["last_heartbeat"] is not None
