"""Running migrations must not change how the rest of the process behaves."""

from __future__ import annotations

import logging

import pytest
import structlog
from sqlalchemy import Engine


@pytest.mark.req("NFR-14")
def test_migrations_leave_application_logging_working(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """Alembic's fileConfig would otherwise disable every logger created before it ran."""
    assert engine is not None  # the fixture applies every migration
    assert logging.getLogger("firebid.request").disabled is False

    with caplog.at_level(logging.INFO, logger="firebid.request"):
        structlog.get_logger("firebid.request").info("after_migrations")
    assert any("after_migrations" in record.getMessage() for record in caplog.records)
