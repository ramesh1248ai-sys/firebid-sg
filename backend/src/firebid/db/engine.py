"""Database engine and sessions. Synchronous SQLAlchemy on psycopg 3 (ADR-001)."""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from firebid.settings import get_settings


def sqlalchemy_url(database_url: str) -> str:
    """Turn a libpq URL (shared with the job queue) into a SQLAlchemy psycopg 3 URL."""
    for prefix in ("postgresql://", "postgres://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url.removeprefix(prefix)
    return database_url


@lru_cache
def get_engine() -> Engine:
    return create_engine(sqlalchemy_url(get_settings().database_url), pool_pre_ping=True)


@lru_cache
def get_service_engine() -> Engine:
    """For jobs that legitimately span bids (deadline alerts, retention upkeep).

    The service role has its own row-level security policy; it still cannot rewrite history.
    """
    settings = get_settings()
    url = settings.database_service_url or settings.database_url
    return create_engine(sqlalchemy_url(url), pool_pre_ping=True)


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@lru_cache
def _service_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_service_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """One unit of work: commits on success, rolls back on any exception."""
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def service_session_scope() -> Iterator[Session]:
    """A unit of work for cross-bid jobs, on the service role."""
    session = _service_session_factory()()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
