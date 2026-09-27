"""Watching a tender set come in (FR-DOC-01, item 11).

Uploading 300 sheets takes minutes, and an estimator who cannot see it happening assumes it
has hung. This is the page they leave open: a count per state, the files that failed with
their reasons, and it updates itself.

**Server-sent events rather than websockets.** The traffic is one-way, it is a handful of
numbers every second or two, and SSE reconnects by itself over plain HTTP. A websocket would
add a protocol upgrade and a heartbeat to own, for nothing this page needs.

**Every state is shown, including the unhappy ones.** A progress bar that reaches 100% while
four files sit in `rejected` is worse than no progress bar, because it says the set is ready
to price when it is not.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

import structlog
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from firebid.api.deps import CurrentBid, DbSession
from firebid.db.engine import session_scope
from firebid.db.identity import acting_as
from firebid.db.models.documents import Document, Sheet

log = structlog.get_logger("firebid.progress")

router = APIRouter(prefix="/bids/{bid_id}", tags=["documents"])

# How often the stream looks for a change. Fast enough to feel live, slow enough that a
# hundred open tabs do not become a hundred queries a second.
POLL_SECONDS = 1.5
# A stream is closed after this, and the browser reconnects. Without it, a forgotten tab
# holds a connection for days.
MAX_STREAM_SECONDS = 1800.0

# The states a document passes through, in the order a person reads them.
STATES = ("received", "awaiting_scan", "processing", "done", "rejected", "quarantined")


class FailedDocument(BaseModel):
    id: uuid.UUID
    filename: str
    state: str
    reason: str | None


class Progress(BaseModel):
    """Where a bid's documents have got to. The counts always sum to `total`."""

    total: int
    counts: dict[str, int]
    sheets: int
    finished: bool
    failures: list[FailedDocument] = []

    @property
    def settled(self) -> int:
        return (
            self.counts.get("done", 0)
            + self.counts.get("rejected", 0)
            + self.counts.get("quarantined", 0)
        )


def read_progress(session: Session, bid_id: uuid.UUID) -> Progress:
    rows = session.execute(
        select(Document.state, func.count())
        .where(Document.bid_id == bid_id)
        .group_by(Document.state)
    ).all()
    counts = {state: 0 for state in STATES}
    for state, count in rows:
        counts[str(state)] = int(count)

    total = sum(counts.values())
    sheets = int(
        session.execute(
            select(func.count()).select_from(Sheet).where(Sheet.bid_id == bid_id)
        ).scalar_one()
    )

    failures = [
        FailedDocument(
            id=document.id,
            filename=document.filename,
            state=document.state,
            reason=document.rejected_reason,
        )
        for document in session.execute(
            select(Document)
            .where(
                Document.bid_id == bid_id,
                Document.state.in_(("rejected", "quarantined", "awaiting_scan")),
            )
            .order_by(Document.filename)
            .limit(200)
        )
        .scalars()
        .all()
    ]

    in_flight = counts["received"] + counts["processing"]
    return Progress(
        total=total,
        counts=counts,
        sheets=sheets,
        # `awaiting_scan` is deliberately not "in flight": a scanner outage can hold a file
        # for hours, and the page should say so rather than spin.
        finished=total > 0 and in_flight == 0,
        failures=failures,
    )


@router.get("/progress", response_model=Progress)
def progress(context: CurrentBid, session: DbSession) -> Progress:
    """Where this bid's documents have got to, once."""
    return read_progress(session, context.bid.id)


@router.get("/progress/stream")
async def progress_stream(context: CurrentBid) -> StreamingResponse:
    """The same thing, as it changes. Sends an event only when something has moved."""
    bid_id = context.bid.id
    user_id = context.principal.user_id

    async def events() -> AsyncIterator[str]:
        last: str | None = None
        elapsed = 0.0
        # The first event goes out immediately, so the page never renders empty.
        while elapsed < MAX_STREAM_SECONDS:
            current = await asyncio.to_thread(_read_json, bid_id, user_id)
            if current != last:
                yield f"data: {current}\n\n"
                last = current
                if json.loads(current)["finished"]:
                    # Nothing more will change without another upload, so let the connection
                    # go rather than polling a settled bid forever.
                    return
            else:
                # A comment keeps proxies from closing an idle connection.
                yield ": still working\n\n"
            await asyncio.sleep(POLL_SECONDS)
            elapsed += POLL_SECONDS

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            # nginx buffers by default, which would hold every event until the stream ends.
            "X-Accel-Buffering": "no",
        },
    )


def _read_json(bid_id: uuid.UUID, user_id: uuid.UUID) -> str:
    """Read the progress on its own connection, off the event loop.

    As the caller: the request's identity is set on the request's session, not this one, and
    row-level security shows a session with no acting user an empty bid.
    """
    with acting_as(user_id), session_scope() as session:
        return read_progress(session, bid_id).model_dump_json()
