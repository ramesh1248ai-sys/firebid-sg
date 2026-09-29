"""Retention, archival and the nightly audit-chain check (NFR-07, NFR-09; P1-11)."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from firebid.db.models.core import Bid, Organisation
from firebid.db.models.review import ActivityMinute
from firebid.domain.actors import Actor
from firebid.domain.state_machines import Role
from firebid.services import effort, retention
from firebid.storage.object_store import MemoryObjectStore
from tests.db.test_audit_chain import write_event
from tests.db.test_sheet_views import member

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


@pytest.mark.req("NFR-07")
def test_time_on_task_is_kept_a_year_and_a_month_then_deleted(
    session: Session, bid: Bid, organisation: Organisation
) -> None:
    person = member(session, organisation, bid, "esther", Role.ESTIMATOR)
    effort.record(session, bid.id, person.user_id, "review", NOW - timedelta(days=401))
    effort.record(session, bid.id, person.user_id, "review", NOW - timedelta(days=10))
    session.commit()

    run = retention.purge(session, NOW)
    session.commit()

    kept = session.execute(
        select(func.count()).select_from(ActivityMinute).where(ActivityMinute.bid_id == bid.id)
    ).scalar_one()
    assert run.deleted["activity_minute"] >= 1
    assert kept == 1


@pytest.mark.req("NFR-09")
def test_an_audit_retention_under_seven_years_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    short = tmp_path / "retention.yaml"
    short.write_text("audit: {retain_years: 3}\n", encoding="utf-8")
    monkeypatch.setattr(retention, "CONFIG", short)
    retention.policy.cache_clear()
    try:
        with pytest.raises(retention.PolicyError, match="under the 7"):
            retention.policy()
    finally:
        retention.policy.cache_clear()


@pytest.mark.req("NFR-09")
def test_old_audit_months_are_archived_once(session: Session, bid: Bid, estimator: Actor) -> None:
    write_event(session, bid, estimator, action="archived step")
    session.commit()
    store = MemoryObjectStore()
    later = datetime.now(UTC) + timedelta(days=31 * 26)

    first = retention.archive_months(session, store, later)
    again = retention.archive_months(session, store, later)

    assert first and again == []
    lines = gzip.decompress(store.get(first[-1])).decode().splitlines()
    assert any(json.loads(line)["action"] == "archived step" for line in lines)


@pytest.mark.req("NFR-09")
def test_the_nightly_check_finds_a_tampered_chain(
    session: Session, bid: Bid, estimator: Actor
) -> None:
    write_event(session, bid, estimator, action="original")
    session.commit()
    assert str(bid.id) not in retention.verify_all_chains(session)

    session.execute(text("ALTER TABLE audit_event DISABLE TRIGGER audit_event_append_only"))
    session.execute(
        text("UPDATE audit_event SET reason = 'quietly changed' WHERE bid_id = :b"), {"b": bid.id}
    )
    session.execute(text("ALTER TABLE audit_event ENABLE TRIGGER audit_event_append_only"))
    session.commit()

    assert str(bid.id) in retention.verify_all_chains(session)
