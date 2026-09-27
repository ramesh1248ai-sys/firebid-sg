# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""Vision assist (P1-05 build item 6): bounded, optional, and never trusted above its cap.

Only clusters the matcher nearly placed (outside the tolerance, within twice it) are sent,
and only when switched on. A detection the model makes enters capped at 0.5 confidence,
which puts it at the top of the review queue, and says it came from vision.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from firebid.ai_gateway.config import load_config
from firebid.ai_gateway.providers.fake import FakeAdapter
from firebid.ai_gateway.router import Router
from firebid.db.models.core import Bid
from firebid.db.models.takeoff import DetectedObject
from firebid.services import detection as detection_service
from firebid.storage.object_store import MemoryObjectStore
from tests.db.test_detection_pipeline import confirm_legend, installation
from tests.db.test_symbol_mapping import CONFIG, no_tiles, store  # noqa: F401

pytestmark = pytest.mark.req("FR-VIS-09")


def switched(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    options = {
        **detection_service.settings(),
        "vision_assist": {
            "enabled": on,
            "near_factor": 2.0,
            "confidence_cap": 0.5,
            "max_per_sheet": 20,
        },
    }
    monkeypatch.setattr(detection_service, "settings", lambda: options)


def queued(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        text("SELECT args FROM procrastinate_jobs WHERE task_name = 'detection.vision'")
    ).scalars()
    return [dict(row) for row in rows]


def router(tmp_path: Path, answer: dict[str, Any]) -> Router:
    path = tmp_path / "llm.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    return Router(
        config=load_config(path),
        adapters={"primary": FakeAdapter("primary").reply(json.dumps(answer))},
        backoff_base_seconds=0,
        sleep=lambda _s: None,
    )


def prepared(session: Session, bid: Bid, store: MemoryObjectStore) -> None:
    installation(session, bid, store, near_misses=8, seed=2)
    confirm_legend(session, bid, store)


def test_it_is_off_by_default(
    session: Session, bid: Bid, store: MemoryObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    switched(monkeypatch, False)
    prepared(session, bid, store)
    before = len(queued(session))

    detection_service.detect_bid(session, store, bid.id)
    session.commit()

    assert len(queued(session)) == before


def test_near_misses_are_sent_and_come_back_capped(
    session: Session,
    bid: Bid,
    store: MemoryObjectStore,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    switched(monkeypatch, True)
    prepared(session, bid, store)
    before = len(queued(session))

    detection_service.detect_bid(session, store, bid.id)
    session.commit()
    jobs = queued(session)[before:]

    assert jobs, "the fixture's near misses are within twice the tolerance"
    assert all(store.exists(job["crop_key"]) for job in jobs)
    job = jobs[0]
    answer = {"object_type": "sprinkler_pendent", "confidence": 0.93, "reason": "a pendent head"}
    found = detection_service.classify_with_vision(
        session,
        store,
        router(tmp_path, answer),
        sheet_id=uuid.UUID(job["sheet_id"]),
        entry_id=uuid.UUID(job["entry_id"]),
        box=job["box"],
        rows=job["rows"],
        crop_key=job["crop_key"],
        distance=job["distance"],
    )
    session.commit()

    assert found is not None
    assert found.extraction_method == "vision"
    assert (found.confidence, found.raw_confidence) == (0.5, 0.93)
    assert found.view_id is not None and found.grid_reference is not None
    assert found.source_ref["crop_key"] == job["crop_key"]

    # Detected again: kept, not asked again, not doubled.
    detection_service.detect_bid(session, store, bid.id)
    session.commit()
    again = detection_service.classify_with_vision(
        session,
        store,
        router(tmp_path, answer),
        sheet_id=found.sheet_id,
        entry_id=uuid.UUID(job["entry_id"]),
        box=job["box"],
        rows=job["rows"],
        crop_key=job["crop_key"],
        distance=job["distance"],
    )
    visions = session.execute(
        select(DetectedObject).where(DetectedObject.extraction_method == "vision")
    ).scalars()
    assert again is None
    assert len(list(visions)) == 1
