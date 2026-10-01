"""The worker's heartbeat beats beside its jobs, not among them (NFR-03)."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from firebid.api.checks import job_queue_check
from firebid.db.system import SystemHeartbeat
from firebid.jobs import heartbeat
from firebid.jobs.app import app

pytestmark = pytest.mark.req("NFR-03")


def last_beat(session: Session) -> datetime | None:
    session.expire_all()
    return session.scalar(
        select(SystemHeartbeat.last_run_at).where(SystemHeartbeat.name == heartbeat.HEARTBEAT_NAME)
    )


def test_a_beat_makes_the_queue_healthy_and_counts_what_waits(session: Session) -> None:
    session.execute(text("DELETE FROM system_heartbeat"))
    session.commit()
    assert not job_queue_check(180)().ok

    depths = heartbeat.beat()

    assert job_queue_check(180)().ok
    assert all(isinstance(count, int) for count in depths.values())
    beaten = last_beat(session)
    assert beaten is not None and (datetime.now(UTC) - beaten).total_seconds() < 60


def test_the_heartbeat_keeps_beating_while_the_caller_is_busy(session: Session) -> None:
    session.execute(text("DELETE FROM system_heartbeat"))
    session.commit()

    stop = heartbeat.start(seconds=0.2)
    try:
        # The caller does nothing for the heartbeat, as a worker deep in a long job does not.
        deadline = time.monotonic() + 10
        first = None
        while first is None and time.monotonic() < deadline:
            time.sleep(0.1)
            first = last_beat(session)
        assert first is not None, "it beats at once"
        later: datetime | None = first
        while later == first and time.monotonic() < deadline:
            time.sleep(0.1)
            later = last_beat(session)
        assert later is not None and later > first, "and again without being asked"
    finally:
        stop.set()


def test_the_heartbeat_is_no_longer_a_scheduled_job() -> None:
    import firebid.jobs.tasks  # noqa: F401  (registers the scheduled tasks)

    scheduled = {task.task.name for task in app.periodic_registry.periodic_tasks.values()}

    assert "system.heartbeat" not in scheduled
    assert "system.deadline_alerts" in scheduled
