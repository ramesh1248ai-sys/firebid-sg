"""Shared API dependencies."""

from collections.abc import Iterator

from sqlalchemy.orm import Session

from firebid.db.engine import session_scope


def get_session() -> Iterator[Session]:
    """One database session per request, committed on success."""
    with session_scope() as session:
        yield session
