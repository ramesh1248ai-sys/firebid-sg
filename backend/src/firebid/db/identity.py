"""The acting user, carried into the database transaction.

Row-level security policies (migration 0003) read `app.user_id`. Every transaction that
touches bid data sets it: requests from the signed-in user, jobs from whoever queued them.
A transaction that forgets sees nothing, which fails loudly instead of leaking.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import Connection, event, text
from sqlalchemy.orm import Session, SessionTransaction

SET_IDENTITY = text("SELECT set_config('app.user_id', :user_id, true)")

current_user_id: ContextVar[uuid.UUID | None] = ContextVar("firebid_current_user_id", default=None)


def set_transaction_identity(session: Session, user_id: uuid.UUID | None) -> None:
    """Set the acting user for the session's current transaction."""
    session.execute(SET_IDENTITY, {"user_id": str(user_id) if user_id else ""})


@contextmanager
def acting_as(user_id: uuid.UUID | None) -> Iterator[None]:
    """Make later sessions in this context act as ``user_id`` (used by jobs)."""
    token = current_user_id.set(user_id)
    try:
        yield
    finally:
        current_user_id.reset(token)


@event.listens_for(Session, "after_begin")
def _apply_identity(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    user_id = current_user_id.get()
    if user_id is not None:
        connection.execute(SET_IDENTITY, {"user_id": str(user_id)})
